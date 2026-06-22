from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes_tasks import _reads_with_blocked
from app.db.session import get_db
from app.schemas.planning import (
    DependencyEdge,
    ProjectGantt,
    WhatIfRequest,
    WhatIfResult,
    WhatIfShift,
)
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


@router.post("/projects/{project_id}/gantt/what-if", response_model=WhatIfResult)
def project_gantt_what_if(
    project_id: int, data: WhatIfRequest, db: Session = Depends(get_db)
) -> WhatIfResult:
    """Preview a staged schedule change without saving it.

    Runs the same pure ``compute_shifts`` the committed PATCH cascade uses, over a
    hypothetical placement set (the project's real tasks with the staged overrides
    layered on), and returns the resulting starts — the overridden tasks plus the
    downstream dependents the cascade pushes. Nothing is persisted; committing a
    what-if is firing the ordinary task PATCHes, which cascade for real. 404s an
    unknown project. (CLAUDE.md prime directive #1: the scheduling math is Python
    and reused, not re-derived in the frontend.)
    """
    if projects_service.get_project(db, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    overrides = [
        planning_service.Override(
            task_id=o.task_id,
            scheduled_start=o.scheduled_start,
            estimated_minutes=o.estimated_minutes,
        )
        for o in data.overrides
    ]
    shifts = planning_service.preview_shifts(db, project_id, overrides)
    logger.info(
        "project_gantt_what_if",
        project_id=project_id,
        override_count=len(overrides),
        shifted_count=len(shifts),
    )
    return WhatIfResult(
        shifts=[
            WhatIfShift(task_id=task_id, scheduled_start=new_start)
            for task_id, new_start in sorted(shifts.items())
        ]
    )
