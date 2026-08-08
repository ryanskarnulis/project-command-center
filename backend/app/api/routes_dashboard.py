from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dashboard import DashboardRead, ProjectOpenTasksRow
from app.services import dashboard as dashboard_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardRead:
    """Aggregate open/done task counts, overall and per project — no model call."""
    total, per_project = dashboard_service.get_overview(db)
    return DashboardRead(
        total_open_tasks=total,
        projects=[
            ProjectOpenTasksRow(
                project_id=row.project.id,
                project_name=row.project.name,
                open_task_count=row.open_count,
                done_task_count=row.done_count,
            )
            for row in per_project
        ],
    )
