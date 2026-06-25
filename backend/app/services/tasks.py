from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    Task,
    TaskDependency,
    TaskPriority,
    TaskReviewStatus,
    TaskWorkflowStatus,
)
from app.services import activity
from app.services import projects as projects_service
from app.services.common import active, deleted, hard_delete, restore, soft_delete


_FILED_REVIEW_STATUSES = {TaskReviewStatus.accepted}

# Fields never propagated to future occurrences by an ``edit_scope="future"``
# patch: a due date is per-occurrence, and a workflow-status change applies only
# to the task the user acted on. (``edit_scope`` is a control flag, not a column,
# and is popped before the forward patch.)
_FORWARD_PATCH_EXCLUDE = {"due_date", "workflow_status"}


class TaskCycleError(ValueError):
    """Setting a task's parent would create a cycle (e.g. A->B->A) or self-parent.

    Nesting is a tree: a task can't be its own ancestor. The caller surfaces a 409.
    """


class DerivedStatusError(ValueError):
    """A status-changing write was attempted on a task whose status is derived.

    A task with accepted subtasks rolls its progress up from them, so it can't be
    marked open/in-progress/done directly. The caller surfaces a 409.
    """


def _default_project_id_for_status(
    db: Session, project_id: int | None, review_status: TaskReviewStatus
) -> int | None:
    if project_id is None and review_status in _FILED_REVIEW_STATUSES:
        return projects_service.ensure_default_project_id(db)
    return project_id


def _log_task_event(db: Session, task: Task, action: str) -> None:
    """Record an activity event for a task, but only once it belongs to a project.

    Extraction creates candidates with ``project_id=None``; logging those would
    flood the per-project feed with rows no feed can show. (Accepting a candidate
    at review files it into a project, but that path commits in bulk in
    ``services.review`` and logs its own ``created`` event there, not through this
    helper.)
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
# estimate and progress summarize its accepted subtasks. Only accepted children
# count — a pending AI breakdown (review_status="candidate") must not flip a
# parent to read-only or pad its estimate before the user approves it.


class Rollup:
    """Derived parent values: estimate is the subtree sum, status is rolled up.

    ``has_subtasks`` is true only when the task has at least one active, accepted
    child; the route uses it both to override the read model and to gate writes.
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


def _children_map(db: Session) -> dict[int | None, list[Task]]:
    """``parent_id -> children`` over all active, accepted tasks (one query)."""
    rows = (
        db.execute(
            active(Task).where(Task.review_status == TaskReviewStatus.accepted)
        )
        .scalars()
        .all()
    )
    by_parent: dict[int | None, list[Task]] = {}
    for row in rows:
        by_parent.setdefault(row.parent_task_id, []).append(row)
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


def compute_rollups(db: Session, tasks: Sequence[Task]) -> dict[int, Rollup]:
    """Derived roll-up per task id, resolved in a single query (no N+1)."""
    by_parent = _children_map(db)
    memo: dict[int, Rollup] = {}
    return {t.id: _resolve_rollup(t, by_parent, memo) for t in tasks}


def get_rollup(db: Session, task: Task) -> Rollup:
    """Derived roll-up for a single task."""
    return compute_rollups(db, [task])[task.id]


def has_active_children(db: Session, task_id: int) -> bool:
    """True if the task has at least one active, accepted subtask.

    Such a task's status/estimate are derived and read-only, so status-changing
    writes against it are rejected.
    """
    return (
        db.execute(
            active(Task)
            .where(
                Task.parent_task_id == task_id,
                Task.review_status == TaskReviewStatus.accepted,
            )
            .limit(1)
        ).first()
        is not None
    )


def list_tasks(
    db: Session,
    project_id: int | None = None,
    review_status: TaskReviewStatus | None = None,
    workflow_status: TaskWorkflowStatus | None = None,
    exclude_done: bool = False,
    top_level_only: bool = False,
) -> Sequence[Task]:
    query = active(Task).order_by(Task.id)
    if project_id is not None:
        query = query.where(Task.project_id == project_id)
    if review_status is not None:
        query = query.where(Task.review_status == review_status)
    if workflow_status is not None:
        query = query.where(Task.workflow_status == workflow_status)
    elif exclude_done:
        query = query.where(Task.workflow_status != TaskWorkflowStatus.done)
    if top_level_only:
        query = query.where(Task.parent_task_id.is_(None))
    return db.execute(query).scalars().all()


def get_task(db: Session, task_id: int) -> Task | None:
    return db.execute(
        active(Task).where(Task.id == task_id)
    ).scalar_one_or_none()


def create_task(
    db: Session,
    *,
    project_id: int | None,
    title: str,
    description: str | None = None,
    review_status: TaskReviewStatus = TaskReviewStatus.accepted,
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open,
    priority: TaskPriority | None = None,
    due_date: date | None = None,
    inbox_item_id: int | None = None,
    confidence: float | None = None,
    assignee_hint: str | None = None,
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
    project_id = _default_project_id_for_status(db, project_id, review_status)
    if parent_task_id is not None:
        _assert_no_parent_cycle(db, None, parent_task_id)
    task = Task(
        project_id=project_id,
        title=title,
        description=description,
        review_status=review_status,
        workflow_status=workflow_status,
        priority=priority,
        due_date=due_date,
        inbox_item_id=inbox_item_id,
        confidence=confidence,
        assignee_hint=assignee_hint,
        parent_task_id=parent_task_id,
        estimated_minutes=estimated_minutes,
    )
    db.add(task)
    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "created")
    return task


def _next_due_date(due_date: date, interval: Mapping[str, Any]) -> date:
    """The next occurrence's due date: ``due_date`` advanced by one interval.

    ``day``/``week`` are exact ``timedelta`` offsets. ``month`` uses manual month
    arithmetic with day-clamping (Jan 31 + 1 month -> Feb 28) since calendar
    months vary in length and ``python-dateutil`` is deliberately not a dependency.
    """
    unit = interval["unit"]
    every = int(interval["every"])
    if unit == "day":
        return due_date + timedelta(days=every)
    if unit == "week":
        return due_date + timedelta(weeks=every)
    if unit == "month":
        month_index = (due_date.month - 1) + every
        year = due_date.year + month_index // 12
        month = month_index % 12 + 1
        last_day = monthrange(year, month)[1]
        return date(year, month, min(due_date.day, last_day))
    raise ValueError(f"Unknown recurrence unit: {unit!r}")


def _clone_subtask_tree(
    db: Session, source: Task, new_parent_id: int, due_date: date | None
) -> None:
    """Recursively clone ``source``'s accepted subtree under ``new_parent_id``.

    Each clone resets to open and carries no recurrence (only the series head is a
    series member); title/description/priority/estimate are copied. Every clone
    inherits ``due_date`` (the new occurrence's date) so the reset checklist is due
    with its occurrence rather than carrying the previous cadence's stale dates.
    Grandchildren recurse. Only active, accepted children are cloned — a pending AI
    breakdown isn't part of the routine until the user approves it.
    """
    for child in list_subtasks(db, source.id):
        if child.review_status != TaskReviewStatus.accepted:
            continue
        clone = Task(
            project_id=child.project_id,
            title=child.title,
            description=child.description,
            priority=child.priority,
            estimated_minutes=child.estimated_minutes,
            repeat_interval=None,
            recurrence_id=None,
            due_date=due_date,
            review_status=TaskReviewStatus.accepted,
            workflow_status=TaskWorkflowStatus.open,
            parent_task_id=new_parent_id,
        )
        db.add(clone)
        db.flush()
        db.refresh(clone)
        _log_task_event(db, clone, "created")
        _clone_subtask_tree(db, child, clone.id, due_date)


def _create_next_occurrence(db: Session, task: Task) -> Task:
    """Clone a completed recurring task as its next open occurrence.

    Copies title/description/priority/estimate/project and the shared
    ``recurrence_id``, advances the due date by one interval, and files the clone
    as an accepted, open, top-level task (occurrences are never subtasks — see the
    sprint's out-of-scope note). The caller guarantees ``repeat_interval`` and
    ``due_date`` are set.

    If the recurring task is a checklist parent, its whole accepted subtree is
    cloned fresh under the new occurrence so a multi-step routine ("weekly release
    checklist") resets for the next cadence. A recurring leaf clones a single row.
    """
    assert task.repeat_interval is not None
    assert task.due_date is not None
    occurrence = Task(
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        priority=task.priority,
        estimated_minutes=task.estimated_minutes,
        repeat_interval=task.repeat_interval,
        recurrence_id=task.recurrence_id,
        due_date=_next_due_date(task.due_date, task.repeat_interval),
        review_status=TaskReviewStatus.accepted,
        workflow_status=TaskWorkflowStatus.open,
        parent_task_id=None,
    )
    db.add(occurrence)
    db.flush()
    db.refresh(occurrence)
    _log_task_event(db, occurrence, "created")
    _clone_subtask_tree(db, task, occurrence.id, occurrence.due_date)
    return occurrence


def _maybe_spawn_recurring_checklist(db: Session, completed_child: Task) -> None:
    """Advance the series when completing a child finishes a recurring checklist.

    A checklist parent's status is derived (read-only), so it never makes the
    stored open->done transition that spawns the next occurrence. Instead, when a
    child completes we walk up to the nearest recurring ancestor and, if its whole
    subtree now rolls up to done, spawn that ancestor's next occurrence. The last
    child to complete is the only one that makes the subtree done, so this fires
    once. Only the nearest recurring ancestor spawns — a series-within-a-series
    can't double-fire.
    """
    visited: set[int] = set()
    current_id = completed_child.parent_task_id
    while current_id is not None and current_id not in visited:
        visited.add(current_id)
        ancestor = get_task(db, current_id)
        if ancestor is None:
            return
        if ancestor.repeat_interval is not None and ancestor.due_date is not None:
            if get_rollup(db, ancestor).workflow_status == TaskWorkflowStatus.done:
                _create_next_occurrence(db, ancestor)
            return
        current_id = ancestor.parent_task_id


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

    # Recurrence requires a due date. The schema can't enforce this (the task may
    # already carry one not present in this request), so re-check against the
    # post-patch view: an incoming due_date wins, else the task's existing one.
    setting_recurrence = (
        "repeat_interval" in control and control["repeat_interval"] is not None
    )
    if setting_recurrence:
        effective_due = (
            control["due_date"] if "due_date" in control else task.due_date
        )
        if effective_due is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A due date is required to set a recurrence",
            )

    prev_workflow = task.workflow_status
    for key, value in control.items():
        setattr(task, key, value)
    task.project_id = _default_project_id_for_status(
        db, task.project_id, task.review_status
    )

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

    # Completing a recurring task spawns its next occurrence. Only on the
    # open->done transition (a no-op re-save of a done task must not duplicate);
    # reopening is a separate path that never deletes.
    becoming_done = (
        control.get("workflow_status") == TaskWorkflowStatus.done
        and prev_workflow != TaskWorkflowStatus.done
    )
    if (
        becoming_done
        and task.repeat_interval is not None
        and task.due_date is not None
    ):
        _create_next_occurrence(db, task)
    elif becoming_done:
        # Completing a child can finish a recurring checklist ancestor whose own
        # status is derived and so never spawns directly.
        _maybe_spawn_recurring_checklist(db, task)

    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "updated")
    return task


def mark_done(db: Session, task: Task) -> Task:
    # Mirror the recurrence behaviour of update_task's open->done transition:
    # POST /tasks/{id}/done is the path the task lists/cards use, so completing a
    # recurring task here must spawn its next occurrence too. (This endpoint has no
    # skip flag — skipping is the detail page's PATCH path.)
    if has_active_children(db, task.id):
        raise DerivedStatusError(
            "This task's status is derived from its subtasks and can't be set directly"
        )
    becoming_done = task.workflow_status != TaskWorkflowStatus.done
    task.workflow_status = TaskWorkflowStatus.done
    task.project_id = _default_project_id_for_status(
        db, task.project_id, task.review_status
    )
    if (
        becoming_done
        and task.repeat_interval is not None
        and task.due_date is not None
    ):
        _create_next_occurrence(db, task)
    elif becoming_done:
        # Completing a child can finish a recurring checklist ancestor whose own
        # status is derived and so never spawns directly.
        _maybe_spawn_recurring_checklist(db, task)
    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "completed")
    return task


def skip_occurrence(db: Session, task: Task) -> Task:
    """Skip the current recurring occurrence: soft-delete it and roll forward.

    "Skip this one" means this occurrence never happened — it's removed from active
    lists (recoverable in trash), and the series continues with the next occurrence.
    Unlike completion, the skipped row is not recorded as ``done`` (that would
    pollute completed-task history with work the user explicitly didn't do).

    Rejects a non-recurring task or one without a due date with a 422: there is
    nothing to roll forward to.
    """
    if task.repeat_interval is None or task.due_date is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only a recurring task with a due date can be skipped",
        )
    next_occurrence = _create_next_occurrence(db, task)
    soft_delete(task)
    db.flush()
    _log_task_event(db, task, "skipped")
    return next_occurrence


def get_series(db: Session, recurrence_id: str) -> list[Task]:
    """All occurrences in a recurrence series, oldest due date first.

    Deliberately a plain ``select(Task)`` rather than the ``active()`` helper:
    skipped occurrences are soft-deleted, but the series timeline must show them
    so the chain is truthful. Ordered by ``due_date`` (then ``id`` as a stable
    tiebreak for rows sharing a date).
    """
    return list(
        db.execute(
            select(Task)
            .where(Task.recurrence_id == recurrence_id)
            .order_by(Task.due_date.asc(), Task.id.asc())
        )
        .scalars()
        .all()
    )


def stop_recurrence(db: Session, task: Task) -> Task:
    """Stop a series from spawning further occurrences.

    Clears ``repeat_interval`` (so completing the task no longer creates the next
    occurrence) while leaving ``recurrence_id`` intact, matching the inline-clear
    rule above so the existing chain stays readable. Rejects a non-recurring task
    with a 422 — there is nothing to stop.
    """
    if task.repeat_interval is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Task is not recurring",
        )
    task.repeat_interval = None
    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "updated")
    return task


def reopen_task(db: Session, task: Task) -> Task:
    if has_active_children(db, task.id):
        raise DerivedStatusError(
            "This task's status is derived from its subtasks and can't be set directly"
        )
    task.workflow_status = TaskWorkflowStatus.open
    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "reopened")
    return task


def soft_delete_task(db: Session, task: Task) -> None:
    # Cascade: deleting a parent removes its whole subtree. Children are
    # soft-deleted depth-first first, so each still logs its own "deleted" event
    # while it still belongs to a project. Restore stays per-task (see
    # restore_task): bringing a parent back does not auto-restore children.
    for child in list_subtasks(db, task.id):
        soft_delete_task(db, child)
    soft_delete(task)
    db.flush()
    _log_task_event(db, task, "deleted")


# --- Trash / restore (Sprint 7) --------------------------------------------


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


def _reschedule_occurrence(db: Session, occurrence: Task, new_due: date) -> None:
    """Set this occurrence and its entire active subtree to ``new_due``.

    Occurrence subtasks all share the occurrence's due date (see
    ``_clone_subtask_tree``), so an un-skip is a flat date reset down the tree.
    """
    occurrence.due_date = new_due
    for child in list_subtasks(db, occurrence.id):  # active children only
        _reschedule_occurrence(db, child, new_due)


def restore_task(db: Session, task: Task) -> Task:
    # Un-skip: if this occurrence's series still has a live occurrence, restoring
    # must NOT add a second one (that's the duplicate-series bug). Pull the live
    # occurrence's date (and its subtasks') back to the restored occurrence's date,
    # then hard-delete the restored row — the series resumes at the un-skipped date
    # with exactly one live occurrence.
    if task.recurrence_id is not None and task.due_date is not None:
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
            _reschedule_occurrence(db, live, task.due_date)
            purge_task(db, task)
            db.flush()
            db.refresh(live)
            _log_task_event(db, live, "restored")
            return live

    # Fallback (non-recurring, or a series with no live occurrence): plain restore.
    # A restored task may point at a since-deleted project; rehome it to General
    # so it stays reachable, mirroring the project-delete rehoming rule.
    if (
        task.project_id is not None
        and projects_service.get_project(db, task.project_id) is None
    ):
        task.project_id = projects_service.ensure_default_project_id(db)
    # An individually-restored task drops its project-cascade marker.
    task.deleted_with_project_id = None
    restore(task)
    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "restored")
    return task


# --- Permanent delete / purge (Sprint 9f) ----------------------------------


def _deleted_subtree_depth_first(db: Session, task: Task) -> list[Task]:
    """The soft-deleted subtree rooted at ``task``, children before parents.

    Soft-deleting a parent cascade-soft-deletes its subtree, so the whole subtree
    sits in trash together; purging the root must take the descendants with it or
    they'd dangle a ``parent_task_id`` at a destroyed row (FK enforcement is off,
    so the DB won't stop us). Children-first ordering lets the caller delete in a
    single pass without tripping the self-referential FK.
    """
    children = (
        db.execute(deleted(Task).where(Task.parent_task_id == task.id))
        .scalars()
        .all()
    )
    ordered: list[Task] = []
    for child in children:
        ordered.extend(_deleted_subtree_depth_first(db, child))
    ordered.append(task)
    return ordered


def purge_task(db: Session, task: Task) -> None:
    """Permanently delete a trashed task and its soft-deleted subtree.

    Cleans the real FK edges first: dependency rows on either side of any subtree
    task, and any stray ``parent_task_id`` from a row outside the purge set (e.g. a
    child that was individually restored while its parent stayed in trash). The
    caller is responsible for committing. ``ai_training_examples`` has no FK to
    tasks and is deliberately untouched.
    """
    subtree = _deleted_subtree_depth_first(db, task)
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

    for node in subtree:  # children before parents
        hard_delete(db, node)
