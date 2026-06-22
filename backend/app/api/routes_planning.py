from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes_tasks import _reads_with_blocked
from app.db.session import get_db
from app.schemas.planning import DependencyEdge, ProjectGantt
from app.services import planning as planning_service
from app.services import projects as projects_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["planning"])


@router.get("/projects/{project_id}/gantt", response_model=ProjectGantt)
def project_gantt(
    project_id: int, db: Session = Depends(get_db)
) -> ProjectGantt:
    """Read-only planning payload: accepted, not-done tasks plus their edges.

    Deterministic read over existing task state — no model call, no schema write.
    Bar geometry is derived in the frontend (``features/planning/ganttModel``);
    here we only gather tasks (with ``is_blocked``/``is_blocking`` for styling)
    and the dependency edges between them. 404s an unknown project.
    """
    if projects_service.get_project(db, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    tasks = planning_service.gantt_tasks(db, project_id)
    edges = planning_service.gantt_dependencies(db, tasks)
    logger.info(
        "project_gantt_read",
        project_id=project_id,
        task_count=len(tasks),
        edge_count=len(edges),
    )
    return ProjectGantt(
        tasks=_reads_with_blocked(db, tasks),
        dependencies=[
            DependencyEdge(
                task_id=edge.task_id,
                depends_on_task_id=edge.depends_on_task_id,
            )
            for edge in edges
        ],
    )
