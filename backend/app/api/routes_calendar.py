from __future__ import annotations

from datetime import date as date_type

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.routes_tasks import _reads_with_blocked
from app.db.session import get_db
from app.schemas.tasks import TaskRead
from app.services import calendar as calendar_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["calendar"])


@router.get("/calendar", response_model=list[TaskRead])
def calendar(
    start: date_type = Query(
        description="First day of the range (YYYY-MM-DD), inclusive"
    ),
    end: date_type = Query(
        description="Last day of the range (YYYY-MM-DD), inclusive"
    ),
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    """Accepted tasks due within ``[start, end]`` for the read-only calendar.

    Deterministic, date-bounded read over existing task state — no model call,
    no schema change. ``end`` before ``start`` is rejected with 422 before any
    query runs. The frontend buckets the flat list onto day cells.
    """
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="end must be on or after start",
        )
    tasks = calendar_service.tasks_in_range(db, start, end)
    logger.info(
        "calendar_range_read",
        start=start.isoformat(),
        end=end.isoformat(),
        count=len(tasks),
    )
    return _reads_with_blocked(db, tasks)
