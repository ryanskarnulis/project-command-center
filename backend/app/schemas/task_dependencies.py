from __future__ import annotations

from pydantic import BaseModel

from app.db.models import TaskStatus


class TaskDependencyCreate(BaseModel):
    depends_on_task_id: int


class TaskDependencyRead(BaseModel):
    """A dependency edge plus the depended-on task's title/status.

    The denormalized title + status let the client render the "Blocked" indicator
    (depended-on task not yet ``done``) without a second round-trip.
    """

    id: int
    task_id: int
    depends_on_task_id: int
    depends_on_title: str
    depends_on_status: TaskStatus
    depends_on_done: bool
