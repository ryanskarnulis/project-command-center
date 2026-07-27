from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency
from app.services import activity
from app.services import projects as projects_service
from app.services.common import active, deleted, hard_delete, restore
from app.services.task_recurrence import (
    find_live_occurrence_on,
    reconcile,
    reschedule_occurrence,
)
from app.services.tasks import OccurrenceConflictError, log_task_event


def list_deleted_tasks(db: Session, *, limit: int = 50) -> Sequence[Task]:
    """Soft-deleted tasks, most-recently-deleted first.

    Excludes tasks cascade-deleted with their project — those belong to the
    project's trash entry and are restored with it, not as standalone rows.
    """
    return (
        db.execute(
            deleted(Task)
            .where(Task.deleted_with_project_id.is_(None))
            .order_by(Task.deleted_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def count_standalone_deleted_tasks(db: Session) -> int:
    """Trashed tasks that are independently restorable (not cascade-deleted with a project)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.deleted_at.is_not(None),
                Task.deleted_with_project_id.is_(None),
            )
        )
        or 0
    )


def get_deleted_task(db: Session, task_id: int) -> Task | None:
    return db.execute(
        deleted(Task).where(Task.id == task_id)
    ).scalar_one_or_none()


def restore_task(db: Session, task: Task, *, defer_reconcile: bool = False) -> Task:
    """Restore one trashed task.

    ``defer_reconcile`` suppresses the recurrence reconciliation this restore
    would normally run, leaving it to the caller (see ``restore_task_subtree``).
    It only applies to the plain-restore path: the un-skip rewind below rewrites
    and destroys rows as one atomic correction and must settle immediately.
    """
    # Un-skip: if this occurrence's series still has a live occurrence, restoring
    # must NOT add a second one (that's the duplicate-series bug). Pull the live
    # occurrence's date (and its subtasks') back to the restored occurrence's date,
    # then hard-delete the restored row — the series resumes at the un-skipped date
    # with exactly one live occurrence.
    #
    # Gated on ``skipped_at``: this rewinds the series and destroys the restored
    # row, which is only what the user meant if they SKIPPED it. A normally-trashed
    # occurrence falls through to the plain restore below and comes back in place.
    if (
        task.skipped_at is not None
        and task.recurrence_id is not None
        and task.due_date is not None
    ):
        # The occurrence that replaced this one when it was skipped: the earliest
        # active sibling due on or after it. Filtering by date avoids retargeting an
        # earlier, already-completed occurrence (e.g. a done checklist parent).
        live = (
            db.execute(
                active(Task)
                .where(
                    Task.recurrence_id == task.recurrence_id,
                    Task.id != task.id,
                    Task.due_date >= task.due_date,
                )
                .order_by(Task.due_date.asc(), Task.id.asc())
            )
            .scalars()
            .first()
        )
        if live is not None:
            reschedule_occurrence(db, live, task.due_date)
            # Unscoped: this destroys the *original* skipped occurrence, which
            # the skip already replaced with fresh clones — including children
            # filed in other projects. The project-scoped walk would leave those
            # merely detached, stranding an obsolete duplicate in standalone
            # trash (BUG #212).
            purge_task(db, task, project_scoped=False)
            db.flush()
            reconcile(db, [live.id])
            db.flush()
            db.refresh(live)
            log_task_event(db, live, "restored")
            return live

    # Plain restore of a series occurrence: refuse if the date is already taken.
    # Skipping (or re-completing) after this row was trashed lands a live
    # replacement on its date, and bringing this one back would leave two active
    # occurrences of one series due the same day — the invariant the
    # uq_tasks_active_occurrence index enforces. Restoring is never allowed to be
    # the write that breaks it; the user trashes or skips the replacement first.
    if task.recurrence_id is not None and task.due_date is not None:
        conflict = find_live_occurrence_on(
            db, task.recurrence_id, task.due_date, exclude_id=task.id
        )
        if conflict is not None:
            raise OccurrenceConflictError(
                f"This series already has an active occurrence due "
                f"{task.due_date.isoformat()}. Trash or skip that one first, "
                f"then restore this."
            )

    # Fallback (non-recurring, or a series with no live occurrence): plain restore.
    # A restored task may point at a since-deleted project; rehome it to General
    # so it stays reachable, mirroring the project-delete rehoming rule.
    if (
        task.project_id is not None
        and projects_service.get_project(db, task.project_id) is None
    ):
        task.project_id = projects_service.ensure_default_project_id(db)
    # An individually-restored task drops its cascade markers: it is back on its
    # own terms and must not be dragged around by a later parent/project restore.
    task.deleted_with_project_id = None
    task.deleted_with_task_id = None
    # And its skip marker: a skipped occurrence whose series has no live successor
    # restores in place through this path, and must not stay flagged as skipped
    # while it's active again.
    task.skipped_at = None
    restore(task)
    db.flush()
    # A restore brings a row back into every derived computation it was absent
    # from: a done occurrence returning to a series whose successor was purged is
    # immediately an effectively-done recurring task with nothing following it,
    # and a restored done child can complete an ancestor's roll-up. Seeding the
    # restored id covers both — reconcile climbs the parent chain and fans out to
    # dependents itself. Idempotent, so restoring an open task is a cheap no-op.
    #
    # A cascade restore defers this: reconciling mid-cascade judges the series
    # against a half-restored subtree (BUG #199).
    if not defer_reconcile:
        reconcile(db, [task.id])
        db.flush()
    db.refresh(task)
    log_task_event(db, task, "restored")
    return task


def restore_task_subtree(db: Session, task: Task) -> tuple[Task, int]:
    """Restore ``task`` **and exactly the descendants trashed with it**.

    ``tasks.soft_delete_task`` cascades, so a single trash of a parent removes a
    whole subtree; restoring only the root leaves the checklist in the trash and
    the parent silently a leaf (BUG #192). This is the true inverse of that one
    delete: it brings back the rows stamped ``deleted_with_task_id == task.id``
    by that cascade and nothing else — descendants the user had already trashed
    independently beforehand carry no marker and stay in the trash, where they
    belong.

    Unlike the purge traversal (``_deleted_subtree_depth_first``) this is *not*
    project-scoped. That walk is scoped because it destroys rows and must not
    reach into another project's trash; here the marker already names the exact
    rows this delete removed, whatever project they live in, and restoring them
    is reversible anyway.

    Returns the restored root and the number of descendants restored with it.
    Each row goes through ``restore_task``, so every one gets its own
    ``restored`` activity event and rehoming.

    Recurrence, however, is reconciled **once, after the whole subtree is back**
    (BUG #199). Reconciling per-row meant the root — restored first, while its
    open children were still deleted and therefore invisible to the roll-up —
    looked effectively done and advanced its series; the checklist then came
    back open behind an already-spawned successor. Deferring to the final state
    keeps the invariant that a successor follows *effective* completion.
    """
    marked_ids = list(
        db.execute(
            select(Task.id)
            .where(
                Task.deleted_at.is_not(None),
                Task.deleted_with_task_id == task.id,
            )
            .order_by(Task.id.asc())
        )
        .scalars()
        .all()
    )
    root = restore_task(db, task, defer_reconcile=True)
    restored_ids = [root.id]
    restored_count = 0
    for descendant_id in marked_ids:
        # Re-read: restoring an un-skipped occurrence root purges rows, so a
        # marked descendant may legitimately be gone by now.
        descendant = get_deleted_task(db, descendant_id)
        if descendant is None:
            continue
        restored = restore_task(db, descendant, defer_reconcile=True)
        restored_ids.append(restored.id)
        restored_count += 1
    # Now that the subtree is whole again, judge the series once against the
    # final effective state. Seeding every restored id keeps the fan-out the
    # per-row reconciles used to provide (ancestors and dependents included).
    reconcile(db, restored_ids)
    db.flush()
    db.refresh(root)
    return root, restored_count


def _deleted_subtree_depth_first(
    db: Session, task: Task, *, project_scoped: bool = True
) -> list[Task]:
    """The soft-deleted subtree rooted at ``task``, children before parents.

    Soft-deleting a parent cascade-soft-deletes its subtree, so the whole subtree
    sits in trash together; purging the root must take the descendants with it or
    they'd dangle a ``parent_task_id`` at a destroyed row. FK enforcement is on
    (``PRAGMA foreign_keys = ON``), so that dangle would *raise*; children-first
    ordering lets the caller delete in a single pass without tripping the
    self-referential FK.

    The walk is **scoped to the root's project** (BUG #189). Task hierarchies may
    cross project boundaries, so an unscoped walk let a purge reach out of the
    project the user selected and permanently destroy a trashed task owned by a
    different, still-active project — work that was independently restorable from
    that project's trash. A descendant in another project is left alone (and cut
    loose by ``purge_task``'s detach step, so no FK dangles); the walk stops
    there, since anything below it hangs off a row that survives.

    ``project_scoped=False`` drops that guard, and is **only** for internal
    corrections that undo a write this service made itself (BUG #212): the
    un-skip rewind must destroy the entire original skipped occurrence, whose
    descendants ``task_recurrence._clone_subtask_tree`` may have filed in other
    projects. Those rows have already been replaced by fresh clones, so leaving
    them detached in another project's trash strands an obsolete duplicate.
    Never pass it for a user-requested purge.
    """
    scope = task.project_id
    conditions = [Task.parent_task_id == task.id]
    if project_scoped:
        conditions.append(
            Task.project_id.is_(None) if scope is None else Task.project_id == scope
        )
    children = db.execute(deleted(Task).where(*conditions)).scalars().all()
    ordered: list[Task] = []
    for child in children:
        ordered.extend(
            _deleted_subtree_depth_first(db, child, project_scoped=project_scoped)
        )
    ordered.append(task)
    return ordered


def deleted_subtree_ids(db: Session, task: Task) -> list[int]:
    """Ids of every row ``purge_task(db, task)`` would destroy (``task`` included).

    Public so callers that need to *count* a purge's true reach before running it
    (see ``trash.purge_selected``) use the same traversal the purge itself does,
    instead of re-deriving it and drifting.
    """
    return [t.id for t in _deleted_subtree_depth_first(db, task)]


def log_task_purged(db: Session, task: Task) -> None:
    """Record the irreversible destruction of ``task`` in the audit log.

    Unlike ``tasks.log_task_event`` this does *not* skip unfiled tasks
    (``project_id is None``). That helper's silence is a feed-noise trade-off for
    reversible edits; a purge is the one mutation nothing can undo, so it is
    always written — with ``project_id=None`` it simply lives outside every
    per-project feed while still being durable history.

    ``activity_events.entity_id`` is a plain column, not a foreign key, so the
    event survives the row it describes. The title is snapshotted into the summary
    because after the purge nothing else remembers it.
    """
    activity.record_event(
        db,
        project_id=task.project_id,
        entity_type="task",
        entity_id=task.id,
        action="purged",
        summary=f'Task "{task.title}" permanently deleted',
    )


def purge_task(db: Session, task: Task, *, project_scoped: bool = True) -> None:
    """Permanently delete a trashed task and its soft-deleted subtree.

    Cleans the real FK edges first: dependency rows on either side of any subtree
    task, and any stray ``parent_task_id`` from a row outside the purge set (e.g. a
    child that was individually restored while its parent stayed in trash, or a
    trashed child owned by another project, which the scoped subtree walk leaves
    behind — see ``_deleted_subtree_depth_first``). The caller is responsible for
    committing.

    Every node in the subtree gets its own ``purged`` audit event *before* the
    row is destroyed — the audit trail must distinguish a restorable soft delete
    from permanent destruction, and cascade-purged descendants are as gone as the
    root the user actually clicked.

    ``project_scoped=False`` is reserved for internal corrections; see
    ``_deleted_subtree_depth_first``.
    """
    subtree = _deleted_subtree_depth_first(db, task, project_scoped=project_scoped)
    ids = [t.id for t in subtree]

    db.execute(
        sql_delete(TaskDependency).where(
            or_(
                TaskDependency.task_id.in_(ids),
                TaskDependency.depends_on_task_id.in_(ids),
            )
        )
    )
    # Detach any row (active or not) still pointing into the purge set but not
    # itself being purged, so no dangling parent ref survives.
    db.execute(
        update(Task)
        .where(Task.parent_task_id.in_(ids), Task.id.not_in(ids))
        .values(parent_task_id=None)
    )

    for node in subtree:
        log_task_purged(db, node)

    for node in subtree:  # children before parents
        hard_delete(db, node)
