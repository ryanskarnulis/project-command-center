from __future__ import annotations

from pydantic import BaseModel

from app.db.models import TaskWorkflowStatus
from app.schemas.common import EntityId, MutationModel


class TaskDependencyCreate(MutationModel):
    depends_on_task_id: EntityId


class TaskDependencyRead(BaseModel):
    """A dependency edge plus the depended-on task's title/workflow state.

    The denormalized title + workflow state let the client render the "Blocked"
    indicator (depended-on task not yet ``done``) without a second round-trip.
    """

    id: int
    task_id: int
    depends_on_task_id: int
    depends_on_title: str
    depends_on_workflow_status: TaskWorkflowStatus
    depends_on_done: bool


class TaskDependentRead(BaseModel):
    """A dependency edge plus the task waiting on this task.

    This is the mirror of ``TaskDependencyRead`` for a task that is blocking
    downstream work: it names each dependent task and exposes whether that task
    is already done.
    """

    id: int
    task_id: int
    dependent_task_id: int
    dependent_title: str
    dependent_workflow_status: TaskWorkflowStatus
    dependent_done: bool


class TaskDependenciesRead(BaseModel):
    """Both directions of one task's dependency graph in a single payload.

    The MCP ``list_dependencies`` tool returns this so an agent sees what the
    task waits on and what waits on it in one call; the REST API keeps its two
    separate endpoints.
    """

    depends_on: list[TaskDependencyRead]
    dependents: list[TaskDependentRead]
