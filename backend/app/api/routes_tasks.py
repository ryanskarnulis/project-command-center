from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import Task
from app.db.session import get_db
from app.schemas.tasks import TaskCreate, TaskRead, TaskUpdate
from app.services import projects as projects_service
from app.services import tasks as tasks_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["tasks"])


def _get_task_or_404(db: Session, task_id: int) -> Task:
    task = tasks_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    return task


def _ensure_project(db: Session, project_id: int) -> None:
    if projects_service.get_project(db, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )


@router.get("/projects/{project_id}/tasks", response_model=list[TaskRead])
def list_tasks(project_id: int, db: Session = Depends(get_db)) -> Sequence[Task]:
    _ensure_project(db, project_id)
    return tasks_service.list_tasks(db, project_id)


@router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    project_id: int, data: TaskCreate, db: Session = Depends(get_db)
) -> Task:
    _ensure_project(db, project_id)
    task = tasks_service.create_task(
        db,
        project_id=project_id,
        title=data.title,
        description=data.description,
        status=data.status,
        priority=data.priority,
        due_date=data.due_date,
    )
    logger.info("task_created", task_id=task.id, project_id=project_id)
    return task


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)) -> Task:
    return _get_task_or_404(db, task_id)


@router.patch("/tasks/{task_id}", response_model=TaskRead)
def update_task(
    task_id: int, data: TaskUpdate, db: Session = Depends(get_db)
) -> Task:
    task = _get_task_or_404(db, task_id)
    updated = tasks_service.update_task(db, task, data.model_dump(exclude_unset=True))
    logger.info("task_updated", task_id=updated.id)
    return updated


@router.post("/tasks/{task_id}/done", response_model=TaskRead)
def mark_task_done(task_id: int, db: Session = Depends(get_db)) -> Task:
    task = _get_task_or_404(db, task_id)
    updated = tasks_service.mark_done(db, task)
    logger.info("task_marked_done", task_id=updated.id)
    return updated


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)) -> None:
    task = _get_task_or_404(db, task_id)
    tasks_service.soft_delete_task(db, task)
    logger.info("task_deleted", task_id=task_id)
