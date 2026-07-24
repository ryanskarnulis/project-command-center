from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db, get_db_write
from app.schemas.trash import (
    EmptyTrashResult,
    ProjectTrashRead,
    PurgeSelectedRequest,
    TrashCountResult,
    TrashRead,
)
from app.services import projects as projects_service
from app.services import task_trash
from app.services import trash as trash_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/trash", tags=["trash"])


@router.get("", response_model=TrashRead)
def get_trash(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> TrashRead:
    """Recently soft-deleted projects and tasks — the restore view."""
    deleted_projects = projects_service.list_deleted_projects(db, limit=limit)
    return TrashRead(
        projects=[
            ProjectTrashRead.model_validate(p).model_copy(
                update={
                    "archived_task_count": projects_service.count_tasks_deleted_with_project(
                        db, p.id
                    )
                }
            )
            for p in deleted_projects
        ],
        tasks=task_trash.list_deleted_tasks(db, limit=limit),  # type: ignore[arg-type]
    )


@router.get("/count", response_model=TrashCountResult)
def get_trash_count(db: Session = Depends(get_db)) -> TrashCountResult:
    """Exact per-kind trash counts for the nav badge (unbounded by the list page)."""
    counts = trash_service.count_trash(db)
    return TrashCountResult(
        projects=counts.projects,
        tasks=counts.tasks,
        purge_total=counts.purge_total,
    )


@router.post("/purge", response_model=EmptyTrashResult)
def purge_selected(
    payload: PurgeSelectedRequest,
    db: Session = Depends(get_db_write),
) -> EmptyTrashResult:
    """Permanently delete the selected trashed rows, in one transaction.

    Skips ids that aren't in trash instead of 404ing: for a bulk purge the end
    state is what matters, and the caller's own cascade is the usual reason an id
    is already gone. The single-item purge routes keep their 404.
    """
    counts = trash_service.purge_selected(
        db,
        project_ids=payload.project_ids,
        task_ids=payload.task_ids,
    )
    db.commit()
    logger.info(
        "trash_purged_selected",
        projects=counts.projects,
        tasks=counts.tasks,
        requested_projects=len(payload.project_ids),
        requested_tasks=len(payload.task_ids),
    )
    return EmptyTrashResult(
        projects=counts.projects,
        tasks=counts.tasks,
    )


@router.delete(
    "",
    response_model=EmptyTrashResult,
)
def empty_trash(db: Session = Depends(get_db_write)) -> EmptyTrashResult:
    """Permanently delete every trashed row. Idempotent; protected projects spared."""
    counts = trash_service.empty_trash(db)
    db.commit()
    logger.info(
        "trash_emptied",
        projects=counts.projects,
        tasks=counts.tasks,
    )
    return EmptyTrashResult(
        projects=counts.projects,
        tasks=counts.tasks,
    )
