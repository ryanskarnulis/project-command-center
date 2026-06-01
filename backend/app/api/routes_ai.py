from __future__ import annotations

from datetime import date

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.workflows.summarize_project import summarize_project_ai
from app.db.session import get_db
from app.schemas.dashboard import (
    DashboardRead,
    ProjectOpenTasksRow,
    ProjectSummaryRead,
    RecentInboxItem,
)
from app.services import dashboard as dashboard_service
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.db.models import TaskStatus

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["ai"])


@router.get("/dashboard", response_model=DashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardRead:
    """Aggregate counts and recent inbox items — no model call."""
    total, per_project, recent = dashboard_service.get_overview(db)
    return DashboardRead(
        total_open_tasks=total,
        projects=[
            ProjectOpenTasksRow(
                project_id=project.id,
                project_name=project.name,
                open_task_count=count,
            )
            for project, count in per_project
        ],
        recent_inbox=[
            RecentInboxItem(
                id=item.id,
                source=item.source,
                summary=item.summary,
                processed_at=item.processed_at,
                reviewed_at=item.reviewed_at,
                resolved_project_id=resolved_pid,
                created_at=item.created_at,
            )
            for item, resolved_pid in recent
        ],
    )


@router.get("/projects/{project_id}/summary", response_model=ProjectSummaryRead)
def get_project_summary(
    project_id: int, db: Session = Depends(get_db)
) -> ProjectSummaryRead:
    """Generate an on-demand plain-text summary for a project's open tasks.

    Calls the ``summary`` AI profile. An Ollama failure returns 502 so the
    dashboard counts (from ``/dashboard``) still render independently.
    """
    project = projects_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    open_tasks = [
        t
        for t in tasks_service.list_tasks(db, project_id)
        if t.status == TaskStatus.accepted
    ]

    log = logger.bind(project_id=project_id)
    try:
        text = summarize_project_ai(
            project_id=project_id,
            project_name=project.name,
            tasks=open_tasks,
            today=date.today(),
        )
    except Exception as exc:
        log.error("summary_upstream_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Summary service unavailable — is Ollama running?",
        ) from exc

    model_name = gateway.get_profile("summary").model
    return ProjectSummaryRead(
        project_id=project_id,
        summary=text,
        model_name=model_name,
    )
