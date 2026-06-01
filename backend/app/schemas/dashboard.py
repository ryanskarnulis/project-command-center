from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import InboxSource


class RecentInboxItem(BaseModel):
    id: int
    source: InboxSource
    summary: str | None
    processed_at: datetime | None
    reviewed_at: datetime | None
    resolved_project_id: int | None
    created_at: datetime


class ProjectOpenTasksRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    project_name: str
    open_task_count: int


class DashboardRead(BaseModel):
    total_open_tasks: int
    projects: list[ProjectOpenTasksRow]
    recent_inbox: list[RecentInboxItem]


class ProjectSummaryRead(BaseModel):
    project_id: int
    summary: str
    model_name: str
