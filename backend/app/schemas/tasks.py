from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import TaskPriority, TaskStatus
from app.schemas.common import NonBlankStr, OptionalStrippedStr


class TaskCreate(BaseModel):
    title: NonBlankStr
    description: OptionalStrippedStr = None
    status: TaskStatus = TaskStatus.accepted
    priority: TaskPriority = TaskPriority.medium
    due_date: date | None = None


class TaskUpdate(BaseModel):
    title: NonBlankStr | None = None
    description: OptionalStrippedStr = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    inbox_item_id: int | None
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    due_date: date | None
    confidence: float | None
    assignee_hint: str | None
    created_at: datetime
    updated_at: datetime
