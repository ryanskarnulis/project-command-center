from __future__ import annotations

from pydantic import BaseModel

from app.db.models import TaskWorkflowStatus


class TaskDependencyCreate(BaseModel):
    depends_on_task_id: int


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
