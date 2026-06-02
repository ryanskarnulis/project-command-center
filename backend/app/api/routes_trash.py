from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.trash import TrashRead
from app.services import inbox as inbox_service
from app.services import projects as projects_service
from app.services import tasks as tasks_service

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
