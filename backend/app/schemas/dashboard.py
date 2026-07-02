from __future__ import annotations


from pydantic import BaseModel, ConfigDict

from app.db.models import InboxSource
from app.schemas.common import UTCDateTime


class RecentInboxItem(BaseModel):
    id: int
    source: InboxSource
    summary: str | None
    processed_at: UTCDateTime | None
    reviewed_at: UTCDateTime | None
    resolved_project_id: int | None
    created_at: UTCDateTime


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
