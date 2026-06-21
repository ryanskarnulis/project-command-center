from __future__ import annotations

from datetime import date as date_type

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.today import TodayPlan
from app.services import today as today_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["today"])

# HH:MM, 24-hour clock. Validated at the boundary so the scheduler's _parse_time
# never sees junk (it does no validation of its own).
_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
# Lower bound is below the assumed-estimate floor but still a usable sliver; upper
# bound is one full day.
_MIN_AVAILABLE_MINUTES = 15
_MAX_AVAILABLE_MINUTES = 1440


@router.get("/today", response_model=TodayPlan)
def today(
    date: date_type | None = Query(
        default=None, description="Target day (YYYY-MM-DD); defaults to server today"
    ),
    start_time: str = Query(
        default=today_service.DEFAULT_START_TIME,
        pattern=_TIME_PATTERN,
        description="Day start (HH:MM, 24h)",
    ),
    available_minutes: int = Query(
        default=today_service.DEFAULT_AVAILABLE_MINUTES,
        ge=_MIN_AVAILABLE_MINUTES,
        le=_MAX_AVAILABLE_MINUTES,
        description="Total planning capacity for the day, in minutes",
    ),
    db: Session = Depends(get_db),
) -> TodayPlan:
    """Deterministic day plan from existing task state. No model calls.

    ``date`` defaults to the server's today when omitted. Bad ``start_time`` or
    out-of-range ``available_minutes`` are rejected with 422 before the scheduler
    runs, so a malformed query never reaches the packing logic.
    """
    target_date = date or date_type.today()
    plan = today_service.get_today_plan(
        db,
        target_date=target_date,
        start_time=start_time,
        available_minutes=available_minutes,
    )
    logger.info(
        "today_plan_generated",
        date=target_date.isoformat(),
        start_time=start_time,
        available_minutes=available_minutes,
        used_minutes=plan.used_minutes,
        scheduled=len(plan.scheduled),
        overflow=len(plan.overflow),
        blocked=len(plan.blocked),
    )
    return plan
