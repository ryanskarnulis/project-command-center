from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependency_reads import dependency_read, dependent_read
from app.db.session import get_db, get_db_write
from app.schemas.task_dependencies import (
    TaskDependencyCreate,
    TaskDependencyRead,
    TaskDependentRead,
)
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["task-dependencies"])


def _get_task_or_404(db: Session, task_id: int) -> None:
    if tasks_service.get_task(db, task_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )


@router.get(
    "/tasks/{task_id}/dependencies", response_model=list[TaskDependencyRead]
)
def list_dependencies(
    task_id: int, db: Session = Depends(get_db)
) -> list[TaskDependencyRead]:
    _get_task_or_404(db, task_id)
    edges = deps_service.list_dependencies(db, task_id)
    effective = deps_service.effective_statuses(
        db, [e.depends_on_task_id for e in edges]
    )
    return [dependency_read(db, e, effective) for e in edges]


@router.get(
    "/tasks/{task_id}/dependents", response_model=list[TaskDependentRead]
)
def list_dependents(
    task_id: int, db: Session = Depends(get_db)
) -> list[TaskDependentRead]:
    _get_task_or_404(db, task_id)
    edges = deps_service.list_dependents(db, task_id)
    effective = deps_service.effective_statuses(db, [e.task_id for e in edges])
    return [dependent_read(db, e, effective) for e in edges]


@router.post(
    "/tasks/{task_id}/dependencies",
    response_model=TaskDependencyRead,
    status_code=status.HTTP_201_CREATED,
)
def add_dependency(
    task_id: int, data: TaskDependencyCreate, db: Session = Depends(get_db_write)
) -> TaskDependencyRead:
    _get_task_or_404(db, task_id)
    try:
        edge = deps_service.add_dependency(db, task_id, data.depends_on_task_id)
    except deps_service.DependencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(edge)
    logger.info(
        "dependency_added",
        task_id=task_id,
        depends_on_task_id=data.depends_on_task_id,
    )
    return dependency_read(db, edge)


@router.delete(
    "/tasks/{task_id}/dependencies/{dependency_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_dependency(
    task_id: int, dependency_id: int, db: Session = Depends(get_db_write)
) -> None:
    edge = deps_service.get_dependency(db, dependency_id)
    if edge is None or edge.task_id != task_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dependency not found"
        )
    deps_service.remove_dependency(db, edge)
    db.commit()
    logger.info("dependency_removed", task_id=task_id, dependency_id=dependency_id)
