from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import TaskDependency, TaskWorkflowStatus
from app.schemas.task_dependencies import TaskDependencyRead, TaskDependentRead
from app.services import tasks as tasks_service


def dependency_read(db: Session, edge: TaskDependency) -> TaskDependencyRead:
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


def dependent_read(db: Session, edge: TaskDependency) -> TaskDependentRead:
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
