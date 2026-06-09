from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Task, TaskPriority, TaskReviewStatus, TaskWorkflowStatus
from app.services import activity
from app.services import projects as projects_service
from app.services.common import active, deleted, restore, soft_delete


_FILED_REVIEW_STATUSES = {TaskReviewStatus.accepted}


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


def update_task(db: Session, task: Task, fields: Mapping[str, Any]) -> Task:
    new_parent_id = fields.get("parent_task_id")
    if new_parent_id is not None:
        _assert_no_parent_cycle(db, task.id, new_parent_id)
    for key, value in fields.items():
        setattr(task, key, value)
    task.project_id = _default_project_id_for_status(
        db, task.project_id, task.review_status
    )
    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "updated")
    return task


def mark_done(db: Session, task: Task) -> Task:
    task.workflow_status = TaskWorkflowStatus.done
    task.project_id = _default_project_id_for_status(
        db, task.project_id, task.review_status
    )
    db.flush()
    db.refresh(task)
    _log_task_event(db, task, "completed")
    return task


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
