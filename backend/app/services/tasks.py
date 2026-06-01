from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Task, TaskPriority, TaskStatus
from app.services import activity
from app.services.common import active, soft_delete


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


def list_tasks(db: Session, project_id: int) -> Sequence[Task]:
    return (
        db.execute(active(Task).where(Task.project_id == project_id).order_by(Task.id))
        .scalars()
        .all()
    )


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
    status: TaskStatus = TaskStatus.accepted,
    priority: TaskPriority = TaskPriority.medium,
    due_date: date | None = None,
    inbox_item_id: int | None = None,
    confidence: float | None = None,
    assignee_hint: str | None = None,
) -> Task:
    task = Task(
        project_id=project_id,
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due_date,
        inbox_item_id=inbox_item_id,
        confidence=confidence,
        assignee_hint=assignee_hint,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _log_task_event(db, task, "created")
    return task


def update_task(db: Session, task: Task, fields: Mapping[str, Any]) -> Task:
    for key, value in fields.items():
        setattr(task, key, value)
    db.commit()
    db.refresh(task)
    _log_task_event(db, task, "updated")
    return task


def mark_done(db: Session, task: Task) -> Task:
    task.status = TaskStatus.done
    db.commit()
    db.refresh(task)
    _log_task_event(db, task, "completed")
    return task


def soft_delete_task(db: Session, task: Task) -> None:
    soft_delete(task)
    db.commit()
    _log_task_event(db, task, "deleted")
