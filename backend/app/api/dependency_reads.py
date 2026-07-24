from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.db.models import TaskDependency, TaskWorkflowStatus
from app.schemas.task_dependencies import TaskDependencyRead, TaskDependentRead
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service

# The status reported for a dependency edge is the *effective* one — rolled-up
# (a checklist parent's stored column is never written back) and blocked-aware
# — matching what the task detail page and every other read surface shows. A
# raw ``task.workflow_status`` would report a fully-done checklist parent as
# "pending" even though the dependent is already unblocked.


def _effective_status(
    db: Session,
    task_id: int,
    effective: Mapping[int, TaskWorkflowStatus] | None,
) -> TaskWorkflowStatus | None:
    """Effective status of ``task_id``, from a precomputed map or resolved here.

    List endpoints pass ``effective`` computed once over every edge target (no
    N+1); single-edge callers (the POST response) pass ``None`` and resolve the
    one id. ``None`` back means the task is missing/soft-deleted.
    """
    if effective is not None:
        return effective.get(task_id)
    return deps_service.effective_statuses(db, [task_id]).get(task_id)


def dependency_read(
    db: Session,
    edge: TaskDependency,
    effective: Mapping[int, TaskWorkflowStatus] | None = None,
) -> TaskDependencyRead:
    depended = tasks_service.get_task(db, edge.depends_on_task_id)
    # The edge's FK target should always resolve to an active task, but guard so a
    # since-deleted target degrades gracefully rather than 500-ing. A missing
    # target is treated as done (nothing left to wait on).
    title = depended.title if depended is not None else "(deleted task)"
    status = _effective_status(db, edge.depends_on_task_id, effective)
    edge_workflow_status = status if status is not None else TaskWorkflowStatus.done
    return TaskDependencyRead(
        id=edge.id,
        task_id=edge.task_id,
        depends_on_task_id=edge.depends_on_task_id,
        depends_on_title=title,
        depends_on_workflow_status=edge_workflow_status,
        depends_on_done=edge_workflow_status == TaskWorkflowStatus.done,
    )


def dependent_read(
    db: Session,
    edge: TaskDependency,
    effective: Mapping[int, TaskWorkflowStatus] | None = None,
) -> TaskDependentRead:
    dependent = tasks_service.get_task(db, edge.task_id)
    # The active edge should always point at an active task, but degrade
    # gracefully if a dependent task was deleted between reads.
    title = dependent.title if dependent is not None else "(deleted task)"
    status = _effective_status(db, edge.task_id, effective)
    edge_workflow_status = status if status is not None else TaskWorkflowStatus.done
    return TaskDependentRead(
        id=edge.id,
        task_id=edge.depends_on_task_id,
        dependent_task_id=edge.task_id,
        dependent_title=title,
        dependent_workflow_status=edge_workflow_status,
        dependent_done=edge_workflow_status == TaskWorkflowStatus.done,
    )
