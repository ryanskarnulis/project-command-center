from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.db.models import (
    Task,
    TaskPriority,
    TaskWorkflowStatus,
)
from app.services import activity
from app.services import projects as projects_service
from app.services.common import active, soft_delete


# Fields never propagated to future occurrences by an ``edit_scope="future"``
# patch. Two kinds:
#   - per-occurrence values: ``due_date`` (each occurrence owns its date) and
#     ``workflow_status`` (completing one occurrence doesn't complete future ones).
#   - structural fields whose per-row invariants the blind bulk UPDATE would
#     bypass: ``parent_task_id`` (occurrences are always top-level, and forwarding
#     it skips ``_assert_no_parent_cycle`` — a crafted patch could self-parent a
#     row), plus ``project_id`` (coupled by ``_default_project_id`` and gated by
#     the derived-status guard, neither of which the bulk UPDATE re-runs). The
#     acted-on row still takes these edits through the guarded setattr loop; only
#     forward propagation is skipped, and the UI never scopes a parent/project
#     edit to "future" anyway.
# (``edit_scope`` is a control flag, not a column, and is popped before the patch.)
_FORWARD_PATCH_EXCLUDE = {
    "due_date",
    "deferred_until",
    "workflow_status",
    "parent_task_id",
    "project_id",
}


class TaskCycleError(ValueError):
    """Setting a task's parent would create a cycle (e.g. A->B->A) or self-parent.

    Nesting is a tree: a task can't be its own ancestor. The caller surfaces a 409.
    """


class DerivedStatusError(ValueError):
    """A status-changing write was attempted on a task whose status is derived.

    A task with subtasks rolls its progress up from them, so it can't be
    marked open/in-progress/done directly. The caller surfaces a 409.
    """


class BlockedTaskError(ValueError):
    """A task was completed while it still has an unfinished dependency.

    A task with an active edge to a not-yet-done task is blocked; it can't be
    marked done until its blockers are. The caller surfaces a 409.
    """


class RecurrenceError(ValueError):
    """A recurrence precondition wasn't met (e.g. recurrence needs a due date, or
    the task isn't recurring). The caller surfaces a 422.
    """


class OccurrenceConflictError(RecurrenceError):
    """A write would put two live occurrences of one series on the same due date.

    A series holds at most one active row per ``(recurrence_id, due_date)`` — the
    invariant behind the ``uq_tasks_active_occurrence`` partial unique index. This
    is a state conflict, not bad input (restoring a trashed occurrence whose date a
    replacement has since taken), so the caller surfaces a 409 rather than the 422
    its ``RecurrenceError`` base gets. It subclasses ``RecurrenceError`` so existing
    handlers still catch it; handlers that want the 409 check it *first*.
    """


def _default_project_id(db: Session, project_id: int | None) -> int | None:
    """Tasks are always filed: no project means the General default project."""
    if project_id is None:
        return projects_service.ensure_default_project_id(db)
    return project_id


def log_task_event(db: Session, task: Task, action: str) -> None:
    """Record an activity event for a task, but only once it belongs to a project.

    A task with ``project_id=None`` is unfiled; logging it would flood the
    per-project feed with rows no feed can show. Filing it into a project later
    logs an ``updated`` event through the normal update path.
    """
    if task.project_id is None:
        return
    activity.record_event(
        db,
        project_id=task.project_id,
        entity_type="task",
        entity_id=task.id,
        action=action,
        summary=f'Task "{task.title}" {action}',
    )


def _assert_no_parent_cycle(
    db: Session, task_id: int | None, new_parent_id: int
) -> None:
    """Reject self-parenting and any ancestry cycle (no A->B->A).

    ``task_id`` is None when creating a brand-new task (no row yet, so it cannot be
    its own ancestor — only the parent's existence is checked). Walks the
    prospective parent's ancestor chain; if ``task_id`` appears, the edge would
    close a cycle. The visited set is a belt-and-suspenders guard against
    pre-existing corruption.
    """
    if task_id is not None and new_parent_id == task_id:
        raise TaskCycleError("A task cannot be its own parent")

    visited: set[int] = set()
    current: int | None = new_parent_id
    while current is not None:
        if current == task_id:
            raise TaskCycleError("Parent assignment would create a cycle")
        if current in visited:
            break
        visited.add(current)
        ancestor = get_task(db, current)
        if ancestor is None:
            raise TaskCycleError("Parent task does not exist")
        current = ancestor.parent_task_id


def is_effective_top_level(task: Task, active_ids: set[int]) -> bool:
    """True when ``task`` behaves as a top-level task.

    A task is top-level with no parent, *or* when its parent is soft-deleted/gone
    — i.e. ``parent_task_id`` is not in ``active_ids``, the set of non-deleted task
    ids. This promotes an orphan (a live child of a trashed parent) so it is still
    scheduled and listed instead of vanishing, mirroring the frontend
    ``taskTree`` orphan-promotion rule. ``active_ids`` must include done-but-active
    parents so a subtask of a merely-completed parent is *not* promoted.
    """
    return task.parent_task_id is None or task.parent_task_id not in active_ids


def effective_top_level_clause() -> ColumnElement[bool]:
    """SQL mirror of :func:`is_effective_top_level`, for ``WHERE`` filtering.

    A row qualifies with no parent, or when no *active* task row carries its
    ``parent_task_id`` (trashed or purged parent). Expressed as a correlated
    ``NOT EXISTS`` on an alias so it stays one query — resolving the parent set in
    Python would be an N+1 on every list read.
    """
    parent = aliased(Task)
    return or_(
        Task.parent_task_id.is_(None),
        ~select(parent.id)
        .where(parent.id == Task.parent_task_id, parent.deleted_at.is_(None))
        .exists(),
    )


def effective_top_level_ids(db: Session, tasks: Sequence[Task]) -> set[int]:
    """Ids of ``tasks`` that are effectively top-level, resolved in one query.

    The batched :func:`is_effective_top_level`: looks up every distinct non-null
    parent id at once and keeps the tasks whose parent is absent from the active
    set. Read-model helpers use this so the wire format carries the same
    promotion rule the service and the frontend boards apply.
    """
    parent_ids = {t.parent_task_id for t in tasks if t.parent_task_id is not None}
    active_parent_ids: set[int] = set()
    if parent_ids:
        active_parent_ids = set(
            db.execute(
                select(Task.id).where(
                    Task.id.in_(parent_ids), Task.deleted_at.is_(None)
                )
            )
            .scalars()
            .all()
        )
    return {t.id for t in tasks if is_effective_top_level(t, active_parent_ids)}


def list_subtasks(db: Session, parent_task_id: int) -> Sequence[Task]:
    """Active direct children of a task, ordered by id."""
    return (
        db.execute(
            active(Task).where(Task.parent_task_id == parent_task_id).order_by(Task.id)
        )
        .scalars()
        .all()
    )


# --- Parent <- child roll-ups (Sprint VVV) ---------------------------------
#
# Derived, never stored (mirrors the ``is_blocked`` precedent): a parent's
# estimate and progress summarize its subtasks.


class Rollup:
    """Derived parent values: estimate is the subtree sum, status is rolled up.

    ``has_subtasks`` is true only when the task has at least one active child;
    the route uses it both to override the read model and to gate writes.
    """

    __slots__ = ("estimated_minutes", "workflow_status", "has_subtasks")

    def __init__(
        self,
        estimated_minutes: int | None,
        workflow_status: TaskWorkflowStatus,
        has_subtasks: bool,
    ) -> None:
        self.estimated_minutes = estimated_minutes
        self.workflow_status = workflow_status
        self.has_subtasks = has_subtasks


def _rollup_status(child_statuses: Sequence[TaskWorkflowStatus]) -> TaskWorkflowStatus:
    """All done -> done; all open -> open; anything mixed/in-progress -> in_progress."""
    if all(s == TaskWorkflowStatus.done for s in child_statuses):
        return TaskWorkflowStatus.done
    if all(s == TaskWorkflowStatus.open for s in child_statuses):
        return TaskWorkflowStatus.open
    return TaskWorkflowStatus.in_progress


def capped_status(
    rollup_status: TaskWorkflowStatus, is_blocked: bool
) -> TaskWorkflowStatus:
    """Cap a rolled-up ``done`` to ``in_progress`` when the task is blocked.

    A checklist parent's completion is derived from its children, so it never
    passes the blocked-gate that leaf completion does; a parent all of whose
    children are done but which itself waits on an unfinished dependency must not
    read as ``done``. Shared by the read model, ``list_tasks`` filtering, and (in
    spirit) ``task_dependencies.effective_statuses`` so every surface agrees.
    """
    if is_blocked and rollup_status == TaskWorkflowStatus.done:
        return TaskWorkflowStatus.in_progress
    return rollup_status


def _children_map_for(
    db: Session, roots: Sequence[Task]
) -> dict[int | None, list[Task]]:
    """``parent_id -> children`` over ``roots`` and all their active descendants.

    Only the requested subtree is loaded, not the whole task table: we descend
    level by level from the root ids, each query an indexed lookup on
    ``parent_task_id``. A leaf read is a single zero-row query. The returned map
    is complete for resolving the roll-up of every task in ``roots`` (which
    ``_resolve_rollup`` only ever walks downward through), and no more.
    """
    by_parent: dict[int | None, list[Task]] = {}
    frontier = [t.id for t in roots]
    seen: set[int] = set(frontier)
    while frontier:
        rows = (
            db.execute(
                active(Task).where(Task.parent_task_id.in_(frontier))
            )
            .scalars()
            .all()
        )
        next_frontier: list[int] = []
        for row in rows:
            by_parent.setdefault(row.parent_task_id, []).append(row)
            if row.id not in seen:
                seen.add(row.id)
                next_frontier.append(row.id)
        frontier = next_frontier
    return by_parent


def _resolve_rollup(
    task: Task,
    by_parent: dict[int | None, list[Task]],
    memo: dict[int, Rollup],
) -> Rollup:
    cached = memo.get(task.id)
    if cached is not None:
        return cached
    children = by_parent.get(task.id, [])
    if not children:
        # Leaf: its own stored values stand.
        rollup = Rollup(task.estimated_minutes, task.workflow_status, False)
        memo[task.id] = rollup
        return rollup
    child_rollups = [_resolve_rollup(c, by_parent, memo) for c in children]
    minutes = [r.estimated_minutes for r in child_rollups if r.estimated_minutes]
    total = sum(minutes) if minutes else None
    status = _rollup_status([r.workflow_status for r in child_rollups])
    rollup = Rollup(total, status, True)
    memo[task.id] = rollup
    return rollup


def _rollups_over(
    tasks: Sequence[Task], by_parent: dict[int | None, list[Task]]
) -> dict[int, Rollup]:
    """Roll-ups for ``tasks`` and, incidentally, every descendant walked to get them.

    ``_resolve_rollup`` memoizes by id as it recurses, so the memo is a complete
    map for the whole subtree — callers wanting only the roots project it down.
    """
    memo: dict[int, Rollup] = {}
    for task in tasks:
        _resolve_rollup(task, by_parent, memo)
    return memo


def compute_rollups(db: Session, tasks: Sequence[Task]) -> dict[int, Rollup]:
    """Derived roll-up per task id, resolved without an N+1 over children.

    ``tasks`` may be any subset, so the child map is read fresh — but scoped to
    the requested subtree (``roots`` + descendants), not the whole task table. A
    caller that already holds the entire active set (the dashboard's open-task
    scan) should use ``compute_rollups_for_full_set`` to skip the reread.

    Keyed by the ids of ``tasks`` only; a caller that also needs the descendants'
    roll-ups should use ``compute_subtree_rollups`` rather than a second pass.
    """
    memo = _rollups_over(tasks, _children_map_for(db, tasks))
    return {t.id: memo[t.id] for t in tasks}


def compute_subtree_rollups(db: Session, roots: Sequence[Task]) -> dict[int, Rollup]:
    """Roll-ups for ``roots`` *and* every active descendant, from one descent.

    Resolving a root already resolves each of its children on the way, so a caller
    that reasons about both (Focus ranks parents, then falls back to their
    subtasks) gets the whole subtree for the cost of ``compute_rollups``.
    """
    return _rollups_over(roots, _children_map_for(db, roots))


def compute_rollups_for_full_set(tasks: Sequence[Task]) -> dict[int, Rollup]:
    """Roll-ups for ``tasks`` when it is already the complete active set.

    Builds the ``parent_id -> children`` map from the passed rows instead of
    re-reading it (``compute_rollups`` re-queries because it accepts subsets), so
    the caller's single fetch is the only query. No database access.
    """
    by_parent: dict[int | None, list[Task]] = {}
    for row in tasks:
        by_parent.setdefault(row.parent_task_id, []).append(row)
    return _rollups_over(tasks, by_parent)


def get_rollup(db: Session, task: Task) -> Rollup:
    """Derived roll-up for a single task."""
    return compute_rollups(db, [task])[task.id]


def has_active_children(db: Session, task_id: int) -> bool:
    """True if the task has at least one active subtask.

    Such a task's status/estimate are derived and read-only, so status-changing
    writes against it are rejected.
    """
    return (
        db.execute(
            active(Task).where(Task.parent_task_id == task_id).limit(1)
        ).first()
        is not None
    )


def _assert_not_blocked(db: Session, task_id: int) -> None:
    """Reject completing a task that still waits on an unfinished dependency.

    Imported locally: ``task_dependencies`` imports this module, so a top-level
    import would cycle.
    """
    from app.services import task_dependencies

    if task_dependencies.is_blocked(db, task_id):
        raise BlockedTaskError(
            "This task is blocked by an unfinished dependency and can't be completed"
        )


def list_tasks(
    db: Session,
    project_id: int | None = None,
    workflow_status: TaskWorkflowStatus | None = None,
    exclude_done: bool = False,
    top_level_only: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> Sequence[Task]:
    # ``limit``/``offset`` page the *effective* result (default: unbounded, for
    # internal callers), so they mean the same thing whether or not a status
    # filter is on. That forces a choice, because the roll-up filter below can
    # only run in Python: page in SQL and the boundary is computed over rows that
    # are about to be discarded (a full page of done tasks filters down to
    # nothing, and — id order being oldest-first — that is exactly what the front
    # of the list holds), or read the matching set whole and slice after
    # filtering. We slice after.
    #
    # The unbounded read that costs is not a new class of query here: the day plan
    # (``focus.list_tasks(db, exclude_done=True)``) and the dashboard already scan
    # the full active set and roll it up on every call, on one user's SQLite file.
    # Correct pages are worth more than the scan. The unfiltered path is exact
    # already, so it keeps paging in SQL and pays nothing.
    filtering = workflow_status is not None or exclude_done

    query = active(Task).order_by(Task.id)
    if project_id is not None:
        query = query.where(Task.project_id == project_id)
    if top_level_only:
        # Effective roots, not raw nullness: a live child whose parent is trashed
        # or purged is promoted (``is_effective_top_level``), so it keeps showing
        # up on boards and in top-level tool reads instead of vanishing with its
        # parent. Filtered in SQL to avoid an N+1 and to keep paging exact.
        query = query.where(effective_top_level_clause())
    if not filtering:
        if offset:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
    tasks = db.execute(query).scalars().all()

    # Status filtering resolves EFFECTIVE (rolled-up) status, not the stored
    # column. A checklist parent's status is derived from its children and never
    # written back, so its stored ``workflow_status`` stays "open" even once every
    # child is done. Filtering the stored column would strand a fully-completed
    # checklist in the open list forever *and* hide it from the completed view
    # (and, conversely, surface a done leaf that a new child re-opened). Resolving
    # the roll-up keeps the filter consistent with the status the read model
    # displays. An explicit ``workflow_status`` takes precedence over
    # ``exclude_done``, matching the callers that set ``exclude_done =
    # workflow_status is None``.
    if filtering and tasks:
        # Local import: task_dependencies imports this module (cycle otherwise).
        from app.services import task_dependencies

        rollups = compute_rollups(db, tasks)
        blocked = task_dependencies.blocked_task_ids(db, [t.id for t in tasks])

        def _effective(task: Task) -> TaskWorkflowStatus:
            return capped_status(rollups[task.id].workflow_status, task.id in blocked)

        if workflow_status is not None:
            tasks = [t for t in tasks if _effective(t) == workflow_status]
        else:
            tasks = [t for t in tasks if _effective(t) != TaskWorkflowStatus.done]
        # The page boundary belongs on the filtered set, not the stored one.
        end = None if limit is None else offset + limit
        tasks = tasks[offset:end]
    return tasks


def get_task(db: Session, task_id: int) -> Task | None:
    return db.execute(
        active(Task).where(Task.id == task_id)
    ).scalar_one_or_none()


def get_tasks(db: Session, task_ids: Iterable[int]) -> dict[int, Task]:
    """Active tasks by id, in one query. Missing/soft-deleted ids are absent.

    The batched ``get_task``, for callers resolving a set of ids they already
    hold — rendering blocker rows, say — instead of one round trip apiece.
    """
    ids = sorted(set(task_ids))
    if not ids:
        return {}
    rows = db.execute(active(Task).where(Task.id.in_(ids))).scalars().all()
    return {task.id: task for task in rows}


def create_task(
    db: Session,
    *,
    project_id: int | None,
    title: str,
    description: str | None = None,
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open,
    priority: TaskPriority | None = None,
    due_date: date | None = None,
    parent_task_id: int | None = None,
    estimated_minutes: int | None = None,
) -> Task:
    # Inherit from the parent on create only. Re-parenting an existing task does
    # not silently move its project (the edit modal exposes an explicit Project
    # field) nor restamp its priority/due date. ``priority``/``due_date`` left
    # unset here seed from the parent as a starting value the caller can override;
    # changing the parent later never clobbers an existing child (see the plan).
    if parent_task_id is not None:
        parent = get_task(db, parent_task_id)
        if parent is not None:
            if project_id is None:
                project_id = parent.project_id
            if priority is None:
                priority = parent.priority
            if due_date is None:
                due_date = parent.due_date
    if priority is None:
        priority = TaskPriority.medium
    project_id = _default_project_id(db, project_id)
    if parent_task_id is not None:
        _assert_no_parent_cycle(db, None, parent_task_id)
    task = Task(
        project_id=project_id,
        title=title,
        description=description,
        workflow_status=workflow_status,
        priority=priority,
        due_date=due_date,
        parent_task_id=parent_task_id,
        estimated_minutes=estimated_minutes,
    )
    db.add(task)
    db.flush()
    db.refresh(task)
    log_task_event(db, task, "created")
    return task


def update_task(db: Session, task: Task, fields: Mapping[str, Any]) -> Task:
    # The edit-scope control flag rides in on the same PATCH but is not a column;
    # pull it out before the attribute loop so it never reaches setattr. (Sprint 9L)
    control = dict(fields)
    edit_scope = control.pop("edit_scope", "this")

    new_parent_id = control.get("parent_task_id")
    if new_parent_id is not None:
        _assert_no_parent_cycle(db, task.id, new_parent_id)

    # A parent's status is derived from its subtasks (read-only); reject a direct
    # workflow-status change rather than silently dropping it.
    if (
        "workflow_status" in control
        and control["workflow_status"] != task.workflow_status
        and has_active_children(db, task.id)
    ):
        raise DerivedStatusError(
            "This task's status is derived from its subtasks and can't be set directly"
        )

    # A blocked task (waiting on an unfinished dependency) can't be completed.
    if (
        control.get("workflow_status") == TaskWorkflowStatus.done
        and task.workflow_status != TaskWorkflowStatus.done
    ):
        _assert_not_blocked(db, task.id)

    # Recurrence requires a due date. The schema can't enforce this (the task may
    # already carry one not present in this request), so re-check against the
    # post-patch view: an incoming value wins, else the task's existing one. This
    # covers both directions — setting a recurrence without a due date, and
    # clearing the due date on a task that stays recurring (which would otherwise
    # leave a series that never spawns and can't be skipped).
    effective_repeat = (
        control["repeat_interval"] if "repeat_interval" in control
        else task.repeat_interval
    )
    effective_due = (
        control["due_date"] if "due_date" in control else task.due_date
    )
    if effective_repeat is not None and effective_due is None:
        raise RecurrenceError("A recurring task requires a due date")

    for key, value in control.items():
        setattr(task, key, value)
    task.project_id = _default_project_id(db, task.project_id)

    # First time recurrence is set, mint the series id; copied to every occurrence.
    # Clearing repeat_interval leaves recurrence_id intact so the chain stays readable.
    if task.repeat_interval is not None and task.recurrence_id is None:
        task.recurrence_id = str(uuid4())

    db.flush()

    # "This and all future occurrences": forward-patch the changed (non-excluded)
    # fields onto same-series rows due on or after this one. Already-done past
    # rows (earlier due dates) are left alone.
    forward_fields = {
        key: value
        for key, value in control.items()
        if key not in _FORWARD_PATCH_EXCLUDE
    }
    if (
        edit_scope == "future"
        and task.recurrence_id is not None
        and task.due_date is not None
        and forward_fields
    ):
        db.execute(
            update(Task)
            .where(
                Task.recurrence_id == task.recurrence_id,
                Task.due_date >= task.due_date,
                Task.deleted_at.is_(None),
            )
            .values(**forward_fields)
        )
        db.expire_all()
        # The bulk UPDATE mutates every forwarded occurrence but emits no activity
        # events, unlike every other multi-row op (which loops and logs per row).
        # Re-select the forwarded rows (same predicate) and log each so the audit
        # trail is complete; the acted-on row is logged once below.
        forwarded = (
            db.execute(
                select(Task).where(
                    Task.recurrence_id == task.recurrence_id,
                    Task.due_date >= task.due_date,
                    Task.deleted_at.is_(None),
                    Task.id != task.id,
                )
            )
            .scalars()
            .all()
        )
        for row in forwarded:
            log_task_event(db, row, "updated")

    # Any patch can move this task (or an ancestor, or something waiting on it)
    # into effective completion: the obvious open->done write, but equally
    # attaching a repeat_interval to a task that is already done, or setting a due
    # date that finally makes a recurring task schedulable. Reconciliation answers
    # "is anything now complete that should have rolled forward?" instead of
    # inferring it from the transition, and is idempotent, so it is safe to call on
    # every update. Imported locally: task_recurrence builds on this module's
    # primitives, so a top-level import would cycle.
    from app.services import task_recurrence

    db.flush()
    task_recurrence.reconcile(db, [task.id])

    db.flush()
    db.refresh(task)
    log_task_event(db, task, "updated")
    return task


def mark_done(db: Session, task: Task) -> Task:
    # Mirror update_task's recurrence behaviour: POST /tasks/{id}/done is the path
    # the task lists/cards use, so completing a recurring task here must roll the
    # series forward too. (This endpoint has no skip flag — skipping is the detail
    # page's PATCH path.) Local import: same deliberate inversion as update_task.
    from app.services import task_recurrence

    if has_active_children(db, task.id):
        raise DerivedStatusError(
            "This task's status is derived from its subtasks and can't be set directly"
        )
    if task.workflow_status != TaskWorkflowStatus.done:
        _assert_not_blocked(db, task.id)
    task.workflow_status = TaskWorkflowStatus.done
    task.project_id = _default_project_id(db, task.project_id)
    db.flush()
    task_recurrence.reconcile(db, [task.id])
    db.flush()
    db.refresh(task)
    log_task_event(db, task, "completed")
    return task


def reopen_task(db: Session, task: Task) -> Task:
    if has_active_children(db, task.id):
        raise DerivedStatusError(
            "This task's status is derived from its subtasks and can't be set directly"
        )
    task.workflow_status = TaskWorkflowStatus.open
    db.flush()
    db.refresh(task)
    log_task_event(db, task, "reopened")
    return task


def soft_delete_task(db: Session, task: Task) -> None:
    # Cascade: deleting a parent removes its whole subtree. Children are
    # soft-deleted depth-first first, so each still logs its own "deleted" event
    # while it still belongs to a project. Restore stays per-task (see
    # task_trash.restore_task): bringing a parent back does not auto-restore
    # children.
    from app.services import task_recurrence

    deleted: set[int] = set()
    seeds: list[int] = []
    _soft_delete_subtree(db, task, deleted, seeds)
    # Reconcile once, after the whole subtree is in the trash, and only from
    # seeds *outside* it. Reconciling mid-cascade let a child's deletion see its
    # still-active recurring parent as (transiently) effectively done and spawn a
    # successor the user never asked for — the parent was on its way to trash.
    # Nodes inside the subtree are gone, so their series must not advance;
    # skip/complete stay the explicit ways to roll a series forward.
    external = [task_id for task_id in seeds if task_id not in deleted]
    if external:
        task_recurrence.reconcile(db, external)


def _soft_delete_subtree(
    db: Session, task: Task, deleted: set[int], seeds: list[int]
) -> None:
    """Soft-delete ``task`` and its descendants depth-first, collecting seeds.

    Records every deleted id in ``deleted`` and every candidate reconciliation
    seed (parents and dependents) in ``seeds``; the caller reconciles what is
    left once the cascade is complete. Trashing the last unfinished child
    completes its parent, and trashing a blocker unblocks whatever waited on it —
    both can roll a series forward, but only for tasks that survive the delete.
    """
    from app.services import task_dependencies as deps_service

    for child in list_subtasks(db, task.id):
        _soft_delete_subtree(db, child, deleted, seeds)
    soft_delete(task)
    db.flush()
    log_task_event(db, task, "deleted")
    deleted.add(task.id)
    # The deleted task itself is gone from reconcile's view (get_task filters
    # soft deletes), so seed the walk from its parent and its dependents.
    seeds.extend(edge.task_id for edge in deps_service.list_dependents(db, task.id))
    if task.parent_task_id is not None:
        seeds.append(task.parent_task_id)
