from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Task, TaskPriority, TaskStatus
from app.services.common import active, soft_delete


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
    project_id: int,
    title: str,
    description: str | None = None,
    status: TaskStatus = TaskStatus.accepted,
    priority: TaskPriority = TaskPriority.medium,
    due_date: date | None = None,
) -> Task:
    task = Task(
        project_id=project_id,
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due_date,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(db: Session, task: Task, fields: Mapping[str, Any]) -> Task:
    for key, value in fields.items():
        setattr(task, key, value)
    db.commit()
    db.refresh(task)
    return task


def mark_done(db: Session, task: Task) -> Task:
    task.status = TaskStatus.done
    db.commit()
    db.refresh(task)
    return task


def soft_delete_task(db: Session, task: Task) -> None:
    soft_delete(task)
    db.commit()
