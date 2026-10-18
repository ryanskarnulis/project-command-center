from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, Project, Task, utcnow
from app.schemas.trash import ProjectRestoreUndo
from app.services import activity
from app.services.common import (
    RestoreUndoConflictError,
    active,
    deleted,
    hard_delete,
    restore,
    soft_delete,
)

DEFAULT_PROJECT_NAME = "General"
DEFAULT_PROJECT_DESCRIPTION = "Default project for unfiled tasks"
DEFAULT_PROJECT_SYSTEM_KEY = "general"


def list_projects(db: Session, *, include_closed: bool = False) -> Sequence[Project]:
    stmt = active(Project)
    if not include_closed:
        stmt = stmt.where(Project.closed_at.is_(None))
    return db.execute(stmt.order_by(Project.sort_order, Project.id)).scalars().all()


def get_project(db: Session, project_id: int) -> Project | None:
    return db.execute(
        active(Project).where(Project.id == project_id)
    ).scalar_one_or_none()


def create_project(db: Session, *, name: str, description: str | None = None) -> Project:
    # New projects land at the end of the manual order.
    next_order = db.execute(
        select(func.coalesce(func.max(Project.sort_order), 0) + 1)
    ).scalar_one()
    project = Project(name=name, description=description, sort_order=next_order)
    db.add(project)
    db.flush()
    db.refresh(project)
    activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="created",
        summary=f'Project "{project.name}" created',
    )
    return project


def get_default_project(db: Session) -> Project | None:
    return db.execute(
        active(Project).where(Project.system_key == DEFAULT_PROJECT_SYSTEM_KEY)
    ).scalar_one_or_none()


def ensure_default_project(db: Session) -> Project:
    """Return General, adopting or creating it if it's somehow missing.

    The real seed is migration ``4f2c8b7d0a1e``, which runs before any request is
    served; General is protected, so nothing can close, trash, or purge it. This is
    a fallback for a database that never saw that migration. Names aren't unique, so
    the adoption lookup takes the oldest match rather than assuming there's one.
    """
    project = get_default_project(db)
    if project is not None:
        return project

    project = (
        db.execute(
            active(Project)
            .where(Project.name == DEFAULT_PROJECT_NAME, Project.system_key.is_(None))
            .order_by(Project.id)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if project is None:
        project = Project(
            name=DEFAULT_PROJECT_NAME,
            description=DEFAULT_PROJECT_DESCRIPTION,
            system_key=DEFAULT_PROJECT_SYSTEM_KEY,
        )
        db.add(project)
    else:
        project.system_key = DEFAULT_PROJECT_SYSTEM_KEY
        if project.description is None:
            project.description = DEFAULT_PROJECT_DESCRIPTION

    db.flush()
    db.refresh(project)
    return project


def ensure_default_project_id(db: Session) -> int:
    return ensure_default_project(db).id


def update_project(db: Session, project: Project, fields: Mapping[str, Any]) -> Project:
    """Apply ``fields`` to ``project``; a patch that changes nothing records nothing.

    An empty patch, or one supplying only the values the row already holds, leaves
    the project exactly as it was — appending an ``updated`` event for it would put
    a change that never happened into the append-only trail (issue #218).
    ``close_project`` / ``reopen_project`` below have always worked this way; this
    is the same rule for the field patch.
    """
    if all(getattr(project, key) == value for key, value in fields.items()):
        return project
    for key, value in fields.items():
        setattr(project, key, value)
    db.flush()
    db.refresh(project)
    activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="updated",
        summary=f'Project "{project.name}" updated',
    )
    return project


def close_project(db: Session, project: Project) -> Project:
    """Close (archive) a project: hidden from the default list, tasks untouched.

    Unlike a soft delete this is instantly reversible via ``reopen_project`` and
    never cascades to tasks. Protected projects (General) can't be closed — they
    are the landing spot for unfiled tasks.
    """
    if project.is_protected:
        raise ValueError(f'Project "{project.name}" is protected and cannot be closed')
    if project.closed_at is None:
        project.closed_at = utcnow()
        db.flush()
        activity.record_event(
            db,
            project_id=project.id,
            entity_type="project",
            entity_id=project.id,
            action="closed",
            summary=f'Project "{project.name}" closed',
        )
    return project


def reopen_project(db: Session, project: Project) -> Project:
    if project.closed_at is not None:
        project.closed_at = None
        # A reopened project rejoins the manual order at the end, like a new one.
        project.sort_order = db.execute(
            select(func.coalesce(func.max(Project.sort_order), 0) + 1)
        ).scalar_one()
        db.flush()
        activity.record_event(
            db,
            project_id=project.id,
            entity_type="project",
            entity_id=project.id,
            action="reopened",
            summary=f'Project "{project.name}" reopened',
        )
    return project


def reorder_projects(db: Session, ordered_ids: Sequence[int]) -> Sequence[Project]:
    """Set the manual project order to ``ordered_ids`` (all open active projects).

    Requires the full active set so a stale client can't silently drop a
    project to the front/back; raises ValueError on any mismatch.
    """
    projects = list_projects(db)
    if sorted(ordered_ids) != sorted(project.id for project in projects):
        raise ValueError("ordered_ids must be exactly the open project ids")

    by_id = {project.id: project for project in projects}
    for position, project_id in enumerate(ordered_ids, start=1):
        by_id[project_id].sort_order = position
    db.flush()
    # One event for the whole reorder; entity_id 0 because no single project
    # owns it and the log schema has no batch notion.
    activity.record_event(
        db,
        project_id=None,
        entity_type="project",
        entity_id=0,
        action="reordered",
        summary="Projects reordered",
    )
    return list_projects(db)


def soft_delete_project(db: Session, project: Project) -> None:
    if project.is_protected:
        raise ValueError(f'Project "{project.name}" is protected and cannot be deleted')

    # Cascade by membership: a deleted project takes exactly its own tasks
    # (``project_id == project.id``) into the trash, each stamped with the project
    # id so ``restore_project`` can bring back the same set and ``list_deleted_tasks``
    # can hide them from the standalone Tasks trash. Task hierarchy is deliberately
    # NOT followed across project boundaries: a child filed under a different,
    # still-active project stays active — it belongs to that project, and its
    # parent landing in trash just leaves it an *effective* top-level task (see
    # ``tasks.is_effective_top_level``). Tasks already trashed independently keep
    # their null marker and are skipped — they aren't in the active set below.
    from app.services import task_dependencies as deps_service
    from app.services import task_recurrence

    deleted_ids: set[int] = set()
    deleted_events: list[tuple[int, str]] = []
    seeds: list[int] = []
    for task in db.execute(
        active(Task).where(Task.project_id == project.id)
    ).scalars().all():
        task.deleted_with_project_id = project.id
        soft_delete(task)
        deleted_ids.add(task.id)
        deleted_events.append((task.id, task.title))
        # Seed reconciliation from the edges that survive this delete: the
        # trashed task is gone from reconcile's view, so walk from its parent
        # and its dependents.
        seeds.extend(edge.task_id for edge in deps_service.list_dependents(db, task.id))
        if task.parent_task_id is not None:
            seeds.append(task.parent_task_id)

    db.flush()
    # One "deleted" event per task, in a single insert: a project can hold tens
    # of thousands of tasks, and the undo of a project restore re-runs this.
    activity.record_task_events(
        db,
        project_id=project.id,
        tasks=deleted_events,
        action="deleted",
        summary_suffix="deleted",
    )
    # Cross-project hierarchy and dependency edges are supported, so trashing
    # this project's tasks can complete a recurring task that lives elsewhere —
    # its last open child or last unfinished blocker just went to the trash.
    # Mirror tasks.soft_delete_task: reconcile once, after the whole batch, and
    # only from seeds *outside* the deleted set (series inside this project must
    # not advance on their way to the trash).
    external = [task_id for task_id in seeds if task_id not in deleted_ids]
    if external:
        task_recurrence.reconcile(db, external)
        db.flush()

    soft_delete(project)
    db.flush()
    activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="deleted",
        summary=f'Project "{project.name}" deleted',
    )


# --- Trash / restore (Sprint 7) --------------------------------------------


def list_deleted_projects(db: Session, *, limit: int = 50) -> Sequence[Project]:
    """Soft-deleted projects, most-recently-deleted first."""
    return (
        db.execute(deleted(Project).order_by(Project.deleted_at.desc()).limit(limit))
        .scalars()
        .all()
    )


def get_deleted_project(db: Session, project_id: int) -> Project | None:
    return db.execute(
        deleted(Project).where(Project.id == project_id)
    ).scalar_one_or_none()


def count_tasks_deleted_with_project(db: Session, project_id: int) -> int:
    """How many trashed tasks would come back if this project is restored with them."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.deleted_at.is_not(None),
                Task.deleted_with_project_id == project_id,
            )
        )
        or 0
    )


def count_tasks_purged_with_project(db: Session, project_id: int) -> int:
    """How many task rows purging this project would permanently destroy.

    Every trashed task the project still owns — not just the ones cascade-deleted
    with it (``count_tasks_deleted_with_project``, which answers the *restore*
    question). ``purge_project`` purges each of them, and their subtree walks stay
    inside this project, so this is exactly the set that disappears. The
    purge confirm needs this number; using the restore count there understated an
    irreversible delete (BUG #189).
    """
    return (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.deleted_at.is_not(None),
                Task.project_id == project_id,
            )
        )
        or 0
    )


def _assert_no_occurrence_conflicts(db: Session, tasks: Sequence[Task]) -> None:
    """Raise ``OccurrenceConflictError`` if restoring ``tasks`` would double-book a series.

    Two ways the batch can break the one-live-occurrence-per-(series, date)
    invariant: a live sibling outside the batch already holds the date, or two
    trashed rows in the batch itself want the same slot (the partial unique index
    only covers active rows, so the trash can hold both).
    """
    from app.services.task_recurrence import find_live_occurrence_on
    from app.services.tasks import OccurrenceConflictError

    claimed: set[tuple[str, date]] = set()
    for task in tasks:
        if task.recurrence_id is None or task.due_date is None:
            continue
        slot = (task.recurrence_id, task.due_date)
        conflict = (
            slot in claimed
            or find_live_occurrence_on(
                db, task.recurrence_id, task.due_date, exclude_id=task.id
            )
            is not None
        )
        if conflict:
            raise OccurrenceConflictError(
                f'Task {task.id} "{task.title}" cannot be restored: its series '
                f"already has an active occurrence due "
                f"{task.due_date.isoformat()}. Trash or skip that one first, then "
                f"restore the project (or restore it without its tasks)."
            )
        claimed.add(slot)


def restore_project(
    db: Session, project: Project, *, restore_tasks: bool = False
) -> tuple[Project, int]:
    """Restore a trashed project; see ``restore_project_with_undo``."""
    restored, count, _ = restore_project_with_undo(
        db, project, restore_tasks=restore_tasks
    )
    return restored, count


def restore_project_with_undo(
    db: Session, project: Project, *, restore_tasks: bool = False
) -> tuple[Project, int, ProjectRestoreUndo]:
    """Restore a trashed project; optionally bring back its cascade-deleted tasks.

    Returns ``(project, restored_task_count, undo)``; ``undo`` is the receipt
    ``undo_project_restore`` replays to reverse exactly this restore (the
    project counterpart of #306/#307). Only tasks stamped with this
    project's id at delete time are pulled back — tasks the user trashed
    independently keep their null marker and stay in the trash.

    When ``restore_tasks`` is False the cascade tasks are NOT stranded: their
    marker is cleared so they move into the standalone Tasks trash, where they
    stay discoverable and individually restorable (into this now-active project).
    Otherwise they'd keep pointing at an active project and be filtered out of
    every trash surface.

    Raises ``OccurrenceConflictError`` (nothing written) when ``restore_tasks`` is
    True and a cascade task is a series occurrence whose date is already held by a
    live sibling.
    """
    from app.services import task_recurrence
    from app.services.tasks import detach_if_parent_cycle

    tasks: Sequence[Task] = ()
    if restore_tasks:
        tasks = db.execute(
            deleted(Task)
            .where(Task.deleted_with_project_id == project.id)
            # Deterministic order: it decides which row a pre-existing parent
            # cycle detaches (``detach_if_parent_cycle`` below).
            .order_by(Task.id.asc())
        ).scalars().all()
        # Same invariant standalone task restore enforces (``task_trash.restore_task``):
        # a restored occurrence must not land on a date a live sibling of its series
        # already holds. Checked for the whole batch *before* any write so a conflict
        # is an actionable domain error instead of the partial unique index raising a
        # raw IntegrityError mid-flush, and so the restore stays atomic.
        _assert_no_occurrence_conflicts(db, tasks)

    restore(project)

    restored_tasks = 0
    restored_ids: list[int] = []
    if restore_tasks:
        for task in tasks:
            restore(task)
            task.deleted_with_project_id = None
            restored_ids.append(task.id)
            restored_tasks += 1
        if restored_ids:
            db.flush()
            # Per-task restore event mirrors the per-task "deleted" events the
            # cascade writes, so an itemized restore is auditable too — and undo
            # reads them back to know which rows this restore returned (#314).
            # One bulk insert: a project can bring back tens of thousands.
            activity.record_task_events(
                db,
                project_id=project.id,
                tasks=[(task.id, task.title) for task in tasks],
                action="restored",
                summary_suffix="restored",
            )
            # Same guard standalone restore runs: a stored parent cycle a pre-#305
            # database may hold through these rows must not come back live (it
            # recursed forever in the roll-up). Checked once the whole batch is
            # active, lowest id first, so one detach breaks each cycle.
            for task in tasks:
                detach_if_parent_cycle(db, task)
            # Same invariant standalone restore keeps (``task_trash.restore_task``):
            # a row coming back out of the trash re-enters every derived computation
            # it was absent from. A done occurrence whose successor was purged while
            # the project sat in trash is immediately an effectively-done recurring
            # task with nothing following it, and a restored done child can complete
            # an ancestor's roll-up. Reconciling once, after the whole batch, keeps
            # it atomic and lets reconcile see the final restored set (it climbs
            # parent chains and fans out to dependents itself). Idempotent, so
            # restored open/non-recurring tasks are a no-op.
            task_recurrence.reconcile(db, restored_ids)
    else:
        # Don't strand the cascade tasks: clearing the marker moves them into the
        # standalone Tasks trash instead of leaving them pointing at an active
        # project (where every trash surface filters them out). Each move is
        # audited as an ``updated`` task event, which is also how undo finds
        # exactly these rows again (#314): the receipt names no ids.
        unarchived = [
            (task_id, title)
            for task_id, title in db.execute(
                select(Task.id, Task.title)
                .where(Task.deleted_with_project_id == project.id)
                .order_by(Task.id.asc())
            ).tuples()
        ]
        db.execute(
            update(Task)
            .where(Task.deleted_with_project_id == project.id)
            .values(deleted_with_project_id=None)
        )
        activity.record_task_events(
            db,
            project_id=project.id,
            tasks=unarchived,
            action=_UNARCHIVED_ACTION,
            summary_suffix="moved to the Tasks trash",
        )

    db.flush()
    db.refresh(project)
    # Logged last, after every task event above: the receipt binds to it, and
    # undo reads this restore's task events as the ones that precede it.
    event = activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="restored",
        summary=f'Project "{project.name}" restored',
    )
    return (
        project,
        restored_tasks,
        ProjectRestoreUndo(project_id=project.id, restore_event_id=event.id),
    )


# The task event a without-tasks restore logs for each row it moves into the
# standalone Tasks trash; undo looks for it to find those rows again.
_UNARCHIVED_ACTION = "updated"


def _restore_window_start(db: Session, project_id: int, restore_event_id: int) -> int:
    """Id of the project ``deleted`` event that the restore ``restore_event_id`` reversed.

    Every task event a project restore logs falls strictly between the two. No
    other task event lands on the project's feed in that window: while the
    project sits in the trash all its tasks do too, and a standalone restore of
    one rehomes it to General before logging.
    """
    start = db.scalar(
        select(func.max(ActivityEvent.id)).where(
            ActivityEvent.entity_type == "project",
            ActivityEvent.entity_id == project_id,
            ActivityEvent.action == "deleted",
            ActivityEvent.id < restore_event_id,
        )
    )
    return 0 if start is None else start


def _restore_task_ids(
    project_id: int, after_id: int, before_id: int, action: str
) -> Select[tuple[int]]:
    """Tasks with an ``action`` event on ``project_id``'s feed in ``(after_id, before_id)``.

    A subquery rather than a materialised id list, so it can feed an ``IN``
    without binding one parameter per task: a project can own more tasks than
    SQLite's bound-variable ceiling.
    """
    return select(ActivityEvent.entity_id).where(
        ActivityEvent.project_id == project_id,
        ActivityEvent.entity_type == "task",
        ActivityEvent.action == action,
        ActivityEvent.id > after_id,
        ActivityEvent.id < before_id,
    )


def _check_project_restore_event(db: Session, project: Project, event_id: int) -> None:
    """Refuse a receipt whose marker is not the project's newest restore (#313, #314)."""
    event = db.get(ActivityEvent, event_id)
    newer = db.scalar(
        select(ActivityEvent.id)
        .where(
            ActivityEvent.entity_type == "project",
            ActivityEvent.entity_id == project.id,
            ActivityEvent.action == "restored",
            ActivityEvent.id > event_id,
        )
        .limit(1)
    )
    if (
        event is None
        or event.entity_type != "project"
        or event.entity_id != project.id
        or event.action != "restored"
        or newer is not None
    ):
        raise RestoreUndoConflictError("This project restore can no longer be undone.")


def undo_project_restore(db: Session, undo: ProjectRestoreUndo) -> None:
    """Reverse exactly what one ``restore_project_with_undo`` changed.

    The project goes back to the trash taking the tasks that restore brought
    back — ``soft_delete_project``'s membership cascade, which is the same set
    once the check below holds — and the tasks it left in the standalone trash
    are archived under it again, so the project's trash entry reads as it did.

    Both sets come from the task events the restore logged between the
    project's ``deleted`` event and its ``restored`` one (#314), so the receipt
    is two ids however many tasks the project owns, and no step binds a
    parameter per task.

    Raises ``RestoreUndoConflictError`` (nothing written) when the receipt is
    not the project's newest restore, the project is no longer active, or its
    active tasks are not exactly the restored set: new or moved-in work would
    be swept into the trash with it, and a restored task trashed or moved out
    since would be misfiled.
    """
    project = get_project(db, undo.project_id)
    if project is None or project.is_protected:
        raise RestoreUndoConflictError("This project restore can no longer be undone.")
    _check_project_restore_event(db, project, undo.restore_event_id)
    start = _restore_window_start(db, project.id, undo.restore_event_id)
    restored_ids = set(
        db.execute(
            _restore_task_ids(project.id, start, undo.restore_event_id, "restored")
        )
        .scalars()
        .all()
    )
    active_ids = set(
        db.execute(
            select(Task.id).where(
                Task.project_id == project.id, Task.deleted_at.is_(None)
            )
        )
        .scalars()
        .all()
    )
    if active_ids != restored_ids:
        raise RestoreUndoConflictError(
            f'"{project.name}" has changed since it was restored; '
            "delete it instead if you still want it in the trash."
        )
    # Before the re-trash below, so only rows still in the trash match: a
    # with-tasks restore can log ``updated`` too (a parent-cycle detach), but on
    # rows it made active, and those go back through the cascade instead.
    db.execute(
        update(Task)
        .where(
            Task.id.in_(
                _restore_task_ids(
                    project.id, start, undo.restore_event_id, _UNARCHIVED_ACTION
                )
            ),
            Task.project_id == project.id,
            Task.deleted_at.is_not(None),
            Task.deleted_with_project_id.is_(None),
        )
        .values(deleted_with_project_id=project.id)
    )
    soft_delete_project(db, project)


# --- Permanent delete / purge (Sprint 9f) ----------------------------------


def purge_project(db: Session, project: Project) -> None:
    """Permanently delete a trashed project and clean every FK edge into it.

    Protected (``General``) is never purgeable. A deleted project's tasks are
    cascade-soft-deleted with it (they keep ``project_id``), so every task still
    pointing here is purged (via ``purge_task`` so their dependency/subtree edges
    go too). The nullable FKs that would otherwise
    dangle — ``activity_events.project_id`` (the audit log, kept but with the ref
    cleared) and any ``tasks.deleted_with_project_id`` still pointing here — are
    nulled.
    ``hard_delete``'s guard enforces the project is already in trash. Caller commits.

    The reach stops at this project's own rows: ``purge_task``'s subtree walk is
    project-scoped, so a trashed task owned by another project that merely hangs
    off one of these tasks is detached, not destroyed (BUG #189). It stays
    individually restorable from its own project's trash.
    """
    from app.services import task_trash  # local: avoid circular import

    if project.is_protected:
        raise ValueError(f'Project "{project.name}" is protected and cannot be deleted')

    # Purge every soft-deleted task still pointing here. A task may already be gone
    # when we reach it (a prior root's subtree purge took it), so re-fetch and skip
    # the misses rather than holding stale ORM rows.
    owned_ids = [
        t.id
        for t in db.execute(
            deleted(Task).where(Task.project_id == project.id)
        ).scalars()
    ]
    for task_id in owned_ids:
        task = db.execute(
            deleted(Task).where(Task.id == task_id)
        ).scalar_one_or_none()
        if task is not None:
            task_trash.purge_task(db, task)

    # Audited before the row is destroyed, and before the ``project_id`` null-out
    # below sweeps it up with the rest of this project's history: the FK forbids
    # keeping the reference, so the purge event identifies the project through
    # ``entity_id`` (a plain column, not a FK) plus the name snapshotted into the
    # summary.
    activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="purged",
        summary=f'Project "{project.name}" permanently deleted',
    )

    db.execute(
        update(ActivityEvent)
        .where(ActivityEvent.project_id == project.id)
        .values(project_id=None)
    )
    db.execute(
        update(Task)
        .where(Task.deleted_with_project_id == project.id)
        .values(deleted_with_project_id=None)
    )

    hard_delete(db, project)
