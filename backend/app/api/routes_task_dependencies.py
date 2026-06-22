from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import TaskDependency, TaskWorkflowStatus
from app.db.session import get_db
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


def _to_read(db: Session, edge: TaskDependency) -> TaskDependencyRead:
    depended = tasks_service.get_task(db, edge.depends_on_task_id)
    # The edge's FK target should always resolve to an active task, but guard so a
    # since-deleted target degrades gracefully rather than 500-ing.
    title = depended.title if depended is not None else "(deleted task)"
    edge_workflow_status = (
        depended.workflow_status if depended is not None else TaskWorkflowStatus.done
    )
    return TaskDependencyRead(
        id=edge.id,
        task_id=edge.task_id,
        depends_on_task_id=edge.depends_on_task_id,
        depends_on_title=title,
        depends_on_workflow_status=edge_workflow_status,
        depends_on_done=edge_workflow_status == TaskWorkflowStatus.done,
    )


def _to_dependent_read(db: Session, edge: TaskDependency) -> TaskDependentRead:
    dependent = tasks_service.get_task(db, edge.task_id)
    # The active edge should always point at an active task, but degrade
    # gracefully if a dependent task was deleted between reads.
    title = dependent.title if dependent is not None else "(deleted task)"
    edge_workflow_status = (
        dependent.workflow_status if dependent is not None else TaskWorkflowStatus.done
    )
    return TaskDependentRead(
        id=edge.id,
        task_id=edge.depends_on_task_id,
        dependent_task_id=edge.task_id,
        dependent_title=title,
        dependent_workflow_status=edge_workflow_status,
        dependent_done=edge_workflow_status == TaskWorkflowStatus.done,
    )


@router.get(
    "/tasks/{task_id}/dependencies", response_model=list[TaskDependencyRead]
)
def list_dependencies(
    task_id: int, db: Session = Depends(get_db)
) -> list[TaskDependencyRead]:
    _get_task_or_404(db, task_id)
    return [_to_read(db, e) for e in deps_service.list_dependencies(db, task_id)]


@router.get(
    "/tasks/{task_id}/dependents", response_model=list[TaskDependentRead]
)
def list_dependents(
    task_id: int, db: Session = Depends(get_db)
) -> list[TaskDependentRead]:
    _get_task_or_404(db, task_id)
    return [
        _to_dependent_read(db, e)
        for e in deps_service.list_dependents(db, task_id)
    ]


@router.post(
    "/tasks/{task_id}/dependencies",
    response_model=TaskDependencyRead,
    status_code=status.HTTP_201_CREATED,
)
def add_dependency(
    task_id: int, data: TaskDependencyCreate, db: Session = Depends(get_db)
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
    return _to_read(db, edge)


@router.delete(
    "/tasks/{task_id}/dependencies/{dependency_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_dependency(
    task_id: int, dependency_id: int, db: Session = Depends(get_db)
) -> None:
    edge = deps_service.get_dependency(db, dependency_id)
    if edge is None or edge.task_id != task_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dependency not found"
        )
    deps_service.remove_dependency(db, edge)
    db.commit()
    logger.info("dependency_removed", task_id=task_id, dependency_id=dependency_id)
