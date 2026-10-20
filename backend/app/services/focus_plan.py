"""The stored day plan: per-day decisions the derived Focus plan honours (#319).

The only write path for ``focus_plan_entries``. Each write lands in
``activity_events`` as ``entity_type="focus_entry"``; removal is a soft delete
and ``restore_entry`` is its undo. The packer that reads these rows lives in
``services/focus.py``; the design is ``docs/focus-day-plan.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FocusPlanEntry, Task, TaskWorkflowStatus
from app.schemas.focus import FocusEntryRead
from app.services import activity
from app.services import tasks as tasks_service
from app.services.common import active, restore, soft_delete

ENTITY_TYPE = "focus_entry"


class FocusEntryError(ValueError):
    """A stored-plan write that the current state can't take (409)."""


def parse_time(value: str) -> int:
    """``HH:MM`` → minutes after midnight. The boundary validates the format."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def format_time(minute: int) -> str:
    hours, minutes = divmod(minute, 60)
    return f"{hours:02d}:{minutes:02d}"


def entry_read(entry: FocusPlanEntry) -> FocusEntryRead:
    return FocusEntryRead(
        id=entry.id,
        plan_date=entry.plan_date,
        task_id=entry.task_id,
        title=entry.title,
        start_time=None if entry.start_minute is None else format_time(entry.start_minute),
        duration_minutes=entry.duration_minutes,
        position=entry.position,
        created_at=entry.created_at,
        deleted_at=entry.deleted_at,
    )


def list_entries(db: Session, plan_date: date) -> list[FocusPlanEntry]:
    """The day's live entries, floating pins in ``position`` order first-come."""
    return list(
        db.execute(
            active(FocusPlanEntry)
            .where(FocusPlanEntry.plan_date == plan_date)
            .order_by(FocusPlanEntry.position, FocusPlanEntry.id)
        ).scalars()
    )


def get_entry(db: Session, entry_id: int) -> FocusPlanEntry | None:
    return db.execute(
        active(FocusPlanEntry).where(FocusPlanEntry.id == entry_id)
    ).scalar_one_or_none()


def get_deleted_entry(db: Session, entry_id: int) -> FocusPlanEntry | None:
    return db.execute(
        select(FocusPlanEntry).where(
            FocusPlanEntry.id == entry_id, FocusPlanEntry.deleted_at.is_not(None)
        )
    ).scalar_one_or_none()


def _live_pin(db: Session, plan_date: date, task_id: int) -> FocusPlanEntry | None:
    return db.execute(
        active(FocusPlanEntry).where(
            FocusPlanEntry.plan_date == plan_date, FocusPlanEntry.task_id == task_id
        )
    ).scalar_one_or_none()


def _next_position(db: Session, plan_date: date) -> int:
    highest = db.scalar(
        select(func.max(FocusPlanEntry.position)).where(
            FocusPlanEntry.plan_date == plan_date, FocusPlanEntry.deleted_at.is_(None)
        )
    )
    return 0 if highest is None else highest + 1


def _ensure_pinnable(db: Session, task: Task) -> None:
    """Only open, effectively top-level work can be pinned.

    A subtask is scheduled as part of its parent's estimate, so pinning one would
    count its minutes twice. Done-ness is the rolled-up one, so a checklist
    parent whose children are all finished is refused too.
    """
    if task.parent_task_id is not None:
        active_ids = set(
            db.execute(
                select(Task.id).where(
                    Task.id == task.parent_task_id, Task.deleted_at.is_(None)
                )
            ).scalars()
        )
        if not tasks_service.is_effective_top_level(task, active_ids):
            raise FocusEntryError(
                f'Task "{task.title}" is a subtask; pin its parent instead'
            )
    rollups = tasks_service.compute_subtree_rollups(db, [task])
    if rollups[task.id].workflow_status == TaskWorkflowStatus.done:
        raise FocusEntryError(f'Task "{task.title}" is done and can\'t be pinned')


def _label(entry: FocusPlanEntry, task_title: str | None) -> str:
    when = "" if entry.start_minute is None else f" at {format_time(entry.start_minute)}"
    if entry.task_id is None:
        return f'Commitment "{entry.title}"{when} on {entry.plan_date.isoformat()}'
    return f'Task "{task_title}" pinned{when} on {entry.plan_date.isoformat()}'


def _record(
    db: Session, entry: FocusPlanEntry, action: str, task: Task | None, verb: str
) -> None:
    activity.record_event(
        db,
        project_id=task.project_id if task is not None else None,
        entity_type=ENTITY_TYPE,
        entity_id=entry.id,
        action=action,
        summary=f"{_label(entry, task.title if task else None)} {verb}",
    )


def _task_of(db: Session, entry: FocusPlanEntry) -> Task | None:
    return None if entry.task_id is None else db.get(Task, entry.task_id)


def pin_task(
    db: Session, *, plan_date: date, task: Task, start_minute: int | None = None
) -> FocusPlanEntry:
    """Pin ``task`` into the day; re-pinning an already pinned task moves its time.

    A floating pin (no ``start_minute``) joins the day ahead of the ranked list;
    a timed one becomes a fixed block. Caller commits.
    """
    _ensure_pinnable(db, task)
    existing = _live_pin(db, plan_date, task.id)
    if existing is not None:
        if existing.start_minute == start_minute:
            return existing
        existing.start_minute = start_minute
        db.flush()
        _record(db, existing, "updated", task, "moved")
        return existing
    entry = FocusPlanEntry(
        plan_date=plan_date,
        task_id=task.id,
        start_minute=start_minute,
        position=_next_position(db, plan_date),
    )
    db.add(entry)
    db.flush()
    _record(db, entry, "created", task, "added to Focus")
    return entry


def add_commitment(
    db: Session,
    *,
    plan_date: date,
    title: str,
    start_minute: int,
    duration_minutes: int,
) -> FocusPlanEntry:
    """Add a fixed-time commitment that is not a task. Caller commits."""
    entry = FocusPlanEntry(
        plan_date=plan_date,
        title=title,
        start_minute=start_minute,
        duration_minutes=duration_minutes,
        position=_next_position(db, plan_date),
    )
    db.add(entry)
    db.flush()
    _record(db, entry, "created", None, "added to Focus")
    return entry


def update_entry(
    db: Session, entry: FocusPlanEntry, fields: dict[str, Any]
) -> FocusPlanEntry:
    """Apply a partial edit (``start_time`` as HH:MM or null, ``title``, ``duration_minutes``).

    A commitment always keeps a time, title and duration; a pin carries only a
    time. Caller commits.
    """
    is_commitment = entry.task_id is None
    if not is_commitment and ({"title", "duration_minutes"} & fields.keys()):
        raise FocusEntryError("A pinned task takes its title and length from the task")
    if is_commitment and any(
        fields.get(key, ...) is None for key in ("start_time", "title", "duration_minutes")
    ):
        raise FocusEntryError("A commitment needs a start time, a title and a length")

    if "start_time" in fields:
        value = fields["start_time"]
        entry.start_minute = None if value is None else parse_time(value)
    if "title" in fields:
        entry.title = fields["title"]
    if "duration_minutes" in fields:
        entry.duration_minutes = fields["duration_minutes"]
    if not fields:
        return entry
    db.flush()
    _record(db, entry, "updated", _task_of(db, entry), "changed")
    return entry


def reorder_pins(
    db: Session, *, plan_date: date, entry_ids: Sequence[int]
) -> list[FocusPlanEntry]:
    """Put the named pins of ``plan_date`` in ``entry_ids`` order (#375).

    ``entry_ids`` may be any subset of the day's live task pins: a client only
    sees the pins the plan currently places, not the ignored ones (done,
    deferred, blocked). The named pins trade the slots they already hold, so
    every other pin keeps its place, and the day's pins are then renumbered
    ``0..n-1`` (which also untangles position ties a restore can leave). A timed
    pin may be named: its position only matters once it floats, which is exactly
    when the plan shows it as floating. A commitment has no order and is
    refused, as is any id that isn't a live entry of the day. Returns the day's
    pins in their new order. Caller commits.
    """
    if not entry_ids:
        raise FocusEntryError("Name at least one pin to reorder")
    if len(set(entry_ids)) != len(entry_ids):
        raise FocusEntryError("Each pin can appear only once in the new order")
    entries = list_entries(db, plan_date)
    by_id = {entry.id: entry for entry in entries}
    for entry_id in entry_ids:
        entry = by_id.get(entry_id)
        if entry is None:
            raise FocusEntryError(
                f"Focus entry {entry_id} is not a live entry of {plan_date.isoformat()}"
            )
        if entry.task_id is None:
            raise FocusEntryError(
                f'Commitment "{entry.title}" sits at its own time and has no order'
            )

    pins = [entry for entry in entries if entry.task_id is not None]
    named = set(entry_ids)
    queue = iter(entry_ids)
    ordered = [by_id[next(queue)] if pin.id in named else pin for pin in pins]
    moved = ordered != pins
    if not moved and all(pin.position == index for index, pin in enumerate(pins)):
        return ordered
    for index, pin in enumerate(ordered):
        pin.position = index
    db.flush()
    if moved:
        titles = dict(
            db.execute(
                select(Task.id, Task.title).where(
                    Task.id.in_([pin.task_id for pin in ordered])
                )
            ).tuples().all()
        )
        sequence = ", ".join(f'"{titles.get(pin.task_id or 0, "?")}"' for pin in ordered)
        # One event for the whole reorder (a drag shifts several rows and the
        # log has no batch notion), so, as with the project order, entity_id 0.
        activity.record_event(
            db,
            project_id=None,
            entity_type=ENTITY_TYPE,
            entity_id=0,
            action="reordered",
            summary=f"Focus pins on {plan_date.isoformat()} reordered: {sequence}",
        )
    return ordered


def remove_entry(db: Session, entry: FocusPlanEntry) -> None:
    """Soft-delete an entry (unpin / drop a commitment). Undo with restore_entry."""
    soft_delete(entry)
    db.flush()
    _record(db, entry, "deleted", _task_of(db, entry), "removed from Focus")


def restore_entry(db: Session, entry: FocusPlanEntry) -> FocusPlanEntry:
    """Bring back a removed entry; refused if the task has been pinned again since."""
    if entry.task_id is not None:
        if _live_pin(db, entry.plan_date, entry.task_id) is not None:
            raise FocusEntryError("That task is already pinned to this day")
        task = tasks_service.get_task(db, entry.task_id)
        if task is None:
            raise FocusEntryError("The pinned task is in the trash")
    restore(entry)
    db.flush()
    _record(db, entry, "restored", _task_of(db, entry), "restored to Focus")
    return entry


def purge_for_tasks(db: Session, task_ids: Sequence[int]) -> None:
    """Remove every entry naming a task that a purge is destroying.

    Purge is the one permanent delete; an entry is meaningless without its task
    and would otherwise block the delete on its foreign key. Caller commits.
    """
    if task_ids:
        db.execute(sql_delete(FocusPlanEntry).where(FocusPlanEntry.task_id.in_(task_ids)))
