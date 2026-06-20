from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.trash import EmptyTrashResult, TrashCountResult, TrashRead
from app.services import inbox as inbox_service
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.services import trash as trash_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/trash", tags=["trash"])


@router.get("", response_model=TrashRead)
def get_trash(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> TrashRead:
    """Recently soft-deleted projects, tasks, and inbox items — the restore view."""
    return TrashRead(
        projects=projects_service.list_deleted_projects(db, limit=limit),  # type: ignore[arg-type]
        tasks=tasks_service.list_deleted_tasks(db, limit=limit),  # type: ignore[arg-type]
        inbox_items=inbox_service.list_deleted_inbox_items(db, limit=limit),  # type: ignore[arg-type]
    )


@router.get("/count", response_model=TrashCountResult)
def get_trash_count(db: Session = Depends(get_db)) -> TrashCountResult:
    """Exact per-kind trash counts for the nav badge (unbounded by the list page)."""
    counts = trash_service.count_trash(db)
    return TrashCountResult(
        projects=counts.projects,
        tasks=counts.tasks,
        inbox_items=counts.inbox_items,
    )


@router.delete("", response_model=EmptyTrashResult)
def empty_trash(db: Session = Depends(get_db)) -> EmptyTrashResult:
    """Permanently delete every trashed row. Idempotent; protected projects spared."""
    counts = trash_service.empty_trash(db)
    db.commit()
    logger.info(
        "trash_emptied",
        projects=counts.projects,
        tasks=counts.tasks,
        inbox_items=counts.inbox_items,
    )
    return EmptyTrashResult(
        projects=counts.projects,
        tasks=counts.tasks,
        inbox_items=counts.inbox_items,
    )
