from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import or_, update
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


def list_tasks(
    db: Session,
    project_id: int | None = None,
    review_status: TaskReviewStatus | None = None,
    workflow_status: TaskWorkflowStatus | None = None,
    exclude_done: bool = False,
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
    priority: TaskPriority = TaskPriority.medium,
    due_date: date | None = None,
    inbox_item_id: int | None = None,
    confidence: float | None = None,
    assignee_hint: str | None = None,
    parent_task_id: int | None = None,
    estimated_minutes: int | None = None,
) -> Task:
    # Inherit project from parent on create only. Re-parenting an existing task
    # does not silently move its project — the edit modal exposes an explicit
    # Project field for that.
    if parent_task_id is not None and project_id is None:
        parent = get_task(db, parent_task_id)
        if parent is not None:
            project_id = parent.project_id
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


def _create_next_occurrence(db: Session, task: Task) -> Task:
    """Clone a completed recurring task as its next open occurrence.

    Copies title/description/priority/estimate/project and the shared
    ``recurrence_id``, advances the due date by one interval, and files the clone
    as an accepted, open, top-level task (occurrences are never subtasks — see the
    sprint's out-of-scope note). The caller guarantees ``repeat_interval`` and
    ``due_date`` are set.
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
    return occurrence


def update_task(db: Session, task: Task, fields: Mapping[str, Any]) -> Task:
    # The edit-scope control flag rides in on the same PATCH but is not a column;
    # pull it out before the attribute loop so it never reaches setattr. (Sprint 9L)
    control = dict(fields)
    edit_scope = control.pop("edit_scope", "this")

    new_parent_id = control.get("parent_task_id")
    if new_parent_id is not None:
        _assert_no_parent_cycle(db, task.id, new_parent_id)

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

    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "updated")
    return task


def mark_done(db: Session, task: Task) -> Task:
    # Mirror the recurrence behaviour of update_task's open->done transition:
    # POST /tasks/{id}/done is the path the task lists/cards use, so completing a
    # recurring task here must spawn its next occurrence too. (This endpoint has no
    # skip flag — skipping is the detail page's PATCH path.)
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


def reopen_task(db: Session, task: Task) -> Task:
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
    """Soft-deleted tasks, most-recently-deleted first."""
    return (
        db.execute(deleted(Task).order_by(Task.deleted_at.desc()).limit(limit))
        .scalars()
        .all()
    )


def get_deleted_task(db: Session, task_id: int) -> Task | None:
    return db.execute(
        deleted(Task).where(Task.id == task_id)
    ).scalar_one_or_none()


def restore_task(db: Session, task: Task) -> Task:
    # A restored task may point at a since-deleted project; rehome it to General
    # so it stays reachable, mirroring the project-delete rehoming rule.
    if (
        task.project_id is not None
        and projects_service.get_project(db, task.project_id) is None
    ):
        task.project_id = projects_service.ensure_default_project_id(db)
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
