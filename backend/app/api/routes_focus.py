from __future__ import annotations

from datetime import date as date_type

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.models import FocusPlanEntry
from app.db.session import get_db, get_db_write
from app.schemas.common import EntityId
from app.schemas.focus import (
    TIME_OF_DAY_PATTERN,
    FocusCommitmentCreate,
    FocusEntryRead,
    FocusEntryUpdate,
    FocusPinCreate,
    FocusPlan,
)
from app.services import focus as focus_service
from app.services import focus_plan as focus_plan_service
from app.services import tasks as tasks_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["focus"])

# HH:MM, 24-hour clock. Validated at the boundary so the scheduler's _parse_time
# never sees junk (it does no validation of its own).
_TIME_PATTERN = TIME_OF_DAY_PATTERN
# Zero is a real window: a session whose capacity finished work has already
# spent asks for what is left, and must get an empty schedule with everything in
# overflow rather than a manufactured minimum (#308). Estimates are positive, so
# nothing packs into 0. Clients still floor a *configured* session at 15. Upper
# bound is one full day.
_MIN_AVAILABLE_MINUTES = 0
_MAX_AVAILABLE_MINUTES = 1440


@router.get("/focus", response_model=FocusPlan)
def focus(
    date: date_type | None = Query(
        default=None, description="Target day (YYYY-MM-DD); defaults to server today"
    ),
    start_time: str = Query(
        default=focus_service.DEFAULT_START_TIME,
        pattern=_TIME_PATTERN,
        description="Day start (HH:MM, 24h)",
    ),
    available_minutes: int = Query(
        default=focus_service.DEFAULT_AVAILABLE_MINUTES,
        ge=_MIN_AVAILABLE_MINUTES,
        le=_MAX_AVAILABLE_MINUTES,
        description="Total planning capacity for the day, in minutes",
    ),
    db: Session = Depends(get_db),
) -> FocusPlan:
    """Deterministic day plan from existing task state. No model calls.

    ``date`` defaults to the server's today when omitted. Bad ``start_time`` or
    out-of-range ``available_minutes`` are rejected with 422 before the scheduler
    runs, so a malformed query never reaches the packing logic.
    """
    target_date = date or date_type.today()
    plan = focus_service.get_focus_plan(
        db,
        target_date=target_date,
        start_time=start_time,
        available_minutes=available_minutes,
    )
    logger.info(
        "focus_plan_generated",
        date=target_date.isoformat(),
        start_time=start_time,
        available_minutes=available_minutes,
        used_minutes=plan.used_minutes,
        scheduled=len(plan.scheduled),
        overflow=len(plan.overflow),
        blocked=len(plan.blocked),
    )
    return plan


# --- The stored day plan (#319) ----------------------------------------------


def _conflict(exc: focus_plan_service.FocusEntryError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _entry_or_404(db: Session, entry_id: int) -> FocusPlanEntry:
    entry = focus_plan_service.get_entry(db, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Focus entry not found"
        )
    return entry


@router.post(
    "/focus/pins", response_model=FocusEntryRead, status_code=status.HTTP_201_CREATED
)
def pin_task(data: FocusPinCreate, db: Session = Depends(get_db_write)) -> FocusEntryRead:
    """Pin a task into a day's plan. Re-pinning an already pinned task moves its time."""
    task = tasks_service.get_task(db, data.task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    start_minute = (
        None if data.start_time is None else focus_plan_service.parse_time(data.start_time)
    )
    try:
        entry = focus_plan_service.pin_task(
            db, plan_date=data.plan_date, task=task, start_minute=start_minute
        )
    except focus_plan_service.FocusEntryError as exc:
        raise _conflict(exc) from exc
    db.commit()
    logger.info("focus_task_pinned", entry_id=entry.id, task_id=task.id)
    return focus_plan_service.entry_read(entry)


@router.post(
    "/focus/commitments",
    response_model=FocusEntryRead,
    status_code=status.HTTP_201_CREATED,
)
def add_commitment(
    data: FocusCommitmentCreate, db: Session = Depends(get_db_write)
) -> FocusEntryRead:
    """Add a fixed-time commitment that is not a task (a meeting)."""
    entry = focus_plan_service.add_commitment(
        db,
        plan_date=data.plan_date,
        title=data.title,
        start_minute=focus_plan_service.parse_time(data.start_time),
        duration_minutes=data.duration_minutes,
    )
    db.commit()
    logger.info("focus_commitment_added", entry_id=entry.id)
    return focus_plan_service.entry_read(entry)


@router.patch("/focus/entries/{entry_id}", response_model=FocusEntryRead)
def update_entry(
    entry_id: EntityId, data: FocusEntryUpdate, db: Session = Depends(get_db_write)
) -> FocusEntryRead:
    entry = _entry_or_404(db, entry_id)
    try:
        focus_plan_service.update_entry(db, entry, data.model_dump(exclude_unset=True))
    except focus_plan_service.FocusEntryError as exc:
        raise _conflict(exc) from exc
    db.commit()
    logger.info("focus_entry_updated", entry_id=entry.id)
    return focus_plan_service.entry_read(entry)


@router.delete("/focus/entries/{entry_id}", response_model=FocusEntryRead)
def remove_entry(entry_id: EntityId, db: Session = Depends(get_db_write)) -> FocusEntryRead:
    """Soft-delete an entry (unpin a task or drop a commitment); undo with restore."""
    entry = _entry_or_404(db, entry_id)
    focus_plan_service.remove_entry(db, entry)
    db.commit()
    logger.info("focus_entry_removed", entry_id=entry.id)
    return focus_plan_service.entry_read(entry)


@router.post("/focus/entries/{entry_id}/restore", response_model=FocusEntryRead)
def restore_entry(entry_id: EntityId, db: Session = Depends(get_db_write)) -> FocusEntryRead:
    entry = focus_plan_service.get_deleted_entry(db, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No removed focus entry with that id"
        )
    try:
        focus_plan_service.restore_entry(db, entry)
    except focus_plan_service.FocusEntryError as exc:
        raise _conflict(exc) from exc
    db.commit()
    logger.info("focus_entry_restored", entry_id=entry.id)
    return focus_plan_service.entry_read(entry)
