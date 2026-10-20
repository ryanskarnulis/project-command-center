"""The Focus session log and running clock (#374).

The only write path for ``focus_sessions``. Each write lands in
``activity_events`` as ``entity_type="focus_session"``; clearing the clock or
taking a finished block back out is a soft delete. Design and table shape:
``docs/focus-day-plan.md`` ("The session log and the running clock").

Two kinds of row share the table: the one live **clock** (``finished_at`` null)
and the day's **finished blocks**. They are written independently, the way the
mobile page drives them — finishing a block books it and stops that block's
clock, undo takes the booking back out and puts the clock back — so each verb
here is idempotent and the order two of them land in doesn't matter.

Routes use the ``BEGIN IMMEDIATE`` write session, so every check-then-insert
below holds against a concurrent writer (a second device).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FocusSession, Task, utcnow
from app.schemas.focus import (
    FocusClockRead,
    FocusClockState,
    FocusFinishedBlockRead,
    FocusSessionLogRead,
)
from app.services import activity
from app.services.common import active, soft_delete

ENTITY_TYPE = "focus_session"


def _as_utc(value: datetime) -> datetime:
    """SQLite hands timestamps back naive; every one this table writes is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def clock_state(clock: FocusSession) -> FocusClockState:
    return FocusClockState.running if clock.leg_started_at is not None else FocusClockState.paused


def elapsed_ms(clock: FocusSession, now: datetime | None = None) -> int:
    """Banked time plus the current leg, never negative (a skewed restore can't go back)."""
    if clock.leg_started_at is None:
        return clock.accumulated_ms
    leg = (now or utcnow()) - _as_utc(clock.leg_started_at)
    return clock.accumulated_ms + max(0, int(leg.total_seconds() * 1000))


def clock_read(clock: FocusSession, now: datetime | None = None) -> FocusClockRead:
    return FocusClockRead(
        id=clock.id,
        plan_date=clock.plan_date,
        task_id=clock.task_id,
        state=clock_state(clock),
        elapsed_ms=elapsed_ms(clock, now),
        leg_started_at=clock.leg_started_at,
    )


def block_read(block: FocusSession, title: str) -> FocusFinishedBlockRead:
    assert block.finished_at is not None
    assert block.minutes is not None and block.planned_minutes is not None
    return FocusFinishedBlockRead(
        id=block.id,
        plan_date=block.plan_date,
        task_id=block.task_id,
        title=title,
        minutes=block.minutes,
        planned_minutes=block.planned_minutes,
        finished_at=block.finished_at,
    )


def _record(db: Session, row: FocusSession, task: Task, action: str, summary: str) -> None:
    activity.record_event(
        db,
        project_id=task.project_id,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        action=action,
        summary=summary,
    )


def _minutes(ms: int) -> int:
    return ms // 60_000


def _task(db: Session, task_id: int) -> Task:
    # Rows outlive a trashed task (soft delete keeps the row), and a purge takes
    # them with it, so the task row is always there to read.
    task = db.get(Task, task_id)
    assert task is not None, f"focus session names missing task {task_id}"
    return task


# --- The clock ----------------------------------------------------------------


def get_clock(db: Session) -> FocusSession | None:
    """The one live clock, on whatever day it was started."""
    return db.execute(
        active(FocusSession).where(FocusSession.finished_at.is_(None))
    ).scalar_one_or_none()


def set_clock(
    db: Session,
    *,
    plan_date: date,
    task: Task,
    state: FocusClockState,
    elapsed_ms: int,
) -> FocusSession:
    """Start, pause, resume or restore the clock on ``task``'s block of ``plan_date``.

    The live clock is updated in place when it is this block's. A clock on any
    other block or day is cleared first: one person works one block at a time,
    and starting the next block is how the last one's stale clock goes away.
    Caller commits (on the ``BEGIN IMMEDIATE`` session).
    """
    now = utcnow()
    leg_started_at = now if state is FocusClockState.running else None
    live = get_clock(db)
    if live is not None and live.task_id == task.id and live.plan_date == plan_date:
        before = clock_state(live)
        unchanged_pause = (
            before is FocusClockState.paused
            and state is FocusClockState.paused
            and live.accumulated_ms == elapsed_ms
        )
        if unchanged_pause:
            return live
        live.accumulated_ms = elapsed_ms
        live.leg_started_at = leg_started_at
        db.flush()
        if state is FocusClockState.running:
            verb = "resumed" if before is FocusClockState.paused else "restarted"
        else:
            verb = "paused"
        _record(
            db,
            live,
            task,
            "updated",
            f'Focus clock on "{task.title}" {verb} at {_minutes(elapsed_ms)} min',
        )
        return live
    if live is not None:
        _clear(db, live, "cleared for the next block")
    clock = FocusSession(
        plan_date=plan_date,
        task_id=task.id,
        accumulated_ms=elapsed_ms,
        leg_started_at=leg_started_at,
    )
    db.add(clock)
    db.flush()
    verb = "started" if state is FocusClockState.running else "set paused"
    _record(
        db,
        clock,
        task,
        "created",
        f'Focus clock on "{task.title}" {verb} at {_minutes(elapsed_ms)} min'
        f" on {plan_date.isoformat()}",
    )
    return clock


def _clear(db: Session, clock: FocusSession, why: str) -> None:
    task = _task(db, clock.task_id)
    soft_delete(clock)
    db.flush()
    _record(
        db,
        clock,
        task,
        "deleted",
        f'Focus clock on "{task.title}" {why} at {_minutes(elapsed_ms(clock))} min',
    )


def clear_clock(
    db: Session, *, task_id: int | None = None, plan_date: date | None = None
) -> FocusSession | None:
    """Clear the live clock; with ``task_id``/``plan_date``, only if it is that block's.

    The filter is what a client passes: a device that is still showing the last
    block must not clear the clock another device has since started on the next.
    Returns the cleared clock, or None when there was nothing (idempotent).
    Caller commits.
    """
    live = get_clock(db)
    if live is None:
        return None
    if task_id is not None and live.task_id != task_id:
        return None
    if plan_date is not None and live.plan_date != plan_date:
        return None
    _clear(db, live, "cleared")
    return live


# --- The finished-block log ---------------------------------------------------


def list_finished(db: Session, plan_date: date) -> list[tuple[FocusSession, str]]:
    """The day's live finished blocks with their task titles, first finished first."""
    rows = db.execute(
        select(FocusSession, Task.title)
        .join(Task, Task.id == FocusSession.task_id)
        .where(
            FocusSession.plan_date == plan_date,
            FocusSession.finished_at.is_not(None),
            FocusSession.deleted_at.is_(None),
        )
        .order_by(FocusSession.finished_at, FocusSession.id)
    ).tuples()
    return [(row, title) for row, title in rows]


def session_log(db: Session, plan_date: date) -> FocusSessionLogRead:
    return FocusSessionLogRead(
        plan_date=plan_date,
        finished=[block_read(row, title) for row, title in list_finished(db, plan_date)],
    )


def _live_block(db: Session, plan_date: date, task_id: int) -> FocusSession | None:
    return db.execute(
        active(FocusSession).where(
            FocusSession.plan_date == plan_date,
            FocusSession.task_id == task_id,
            FocusSession.finished_at.is_not(None),
        )
    ).scalar_one_or_none()


def finish_block(
    db: Session, *, plan_date: date, task: Task, minutes: int, planned_minutes: int
) -> FocusSession:
    """Book ``task``'s block into ``plan_date``'s log; booking it again re-books it.

    Also stops the block's own clock, if it is the live one — the finish is what
    the clock was measuring. A clock on any other block is left alone (finishing
    a later row from its sheet must not cost the current block its time).
    Caller commits.
    """
    live = get_clock(db)
    if live is not None and live.task_id == task.id and live.plan_date == plan_date:
        _clear(db, live, "stopped by the finish")
    block = _live_block(db, plan_date, task.id)
    if block is not None:
        if block.minutes == minutes and block.planned_minutes == planned_minutes:
            return block
        block.minutes = minutes
        block.planned_minutes = planned_minutes
        block.finished_at = utcnow()
        db.flush()
        _record(
            db,
            block,
            task,
            "updated",
            f'Focus block "{task.title}" re-booked at {minutes} min'
            f" on {plan_date.isoformat()}",
        )
        return block
    block = FocusSession(
        plan_date=plan_date,
        task_id=task.id,
        finished_at=utcnow(),
        minutes=minutes,
        planned_minutes=planned_minutes,
    )
    db.add(block)
    db.flush()
    _record(
        db,
        block,
        task,
        "created",
        f'Focus block "{task.title}" finished in {minutes} min'
        f" (planned {planned_minutes}) on {plan_date.isoformat()}",
    )
    return block


def remove_block(db: Session, *, plan_date: date, task_id: int) -> FocusSession | None:
    """Take a finished block back out (the undo of a completion). Idempotent.

    Caller commits.
    """
    block = _live_block(db, plan_date, task_id)
    if block is None:
        return None
    task = _task(db, task_id)
    soft_delete(block)
    db.flush()
    _record(
        db,
        block,
        task,
        "deleted",
        f'Focus block "{task.title}" taken out of {plan_date.isoformat()}',
    )
    return block


def purge_for_tasks(db: Session, task_ids: Sequence[int]) -> None:
    """Remove every session row naming a task that a purge is destroying.

    Purge is the one permanent delete; a row is meaningless without its task and
    would otherwise block the delete on its foreign key. Caller commits.
    """
    if task_ids:
        db.execute(sql_delete(FocusSession).where(FocusSession.task_id.in_(task_ids)))
