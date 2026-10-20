"""The stored day plan: per-day decisions the derived Focus plan honours (#319).

The only write path for ``focus_plan_entries``. Each write lands in
``activity_events`` as ``entity_type="focus_entry"``; removal is a soft delete
and ``restore_entry`` is its undo. A repeating commitment is one series row
whose occurrences are worked out per day on read (``day_commitments``, #376);
skipping one day stores a skip row for that day. The packer that reads these
rows lives in ``services/focus.py``; the design is ``docs/focus-day-plan.md``.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FocusPlanEntry, Task, TaskWorkflowStatus
from app.schemas.focus import CommitmentRepeatInterval, FocusEntryRead
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
        repeat_interval=CommitmentRepeatInterval.model_validate(entry.repeat_interval)
        if entry.repeat_interval is not None
        else None,
        series_id=entry.series_id,
        created_at=entry.created_at,
        deleted_at=entry.deleted_at,
    )


# --- Repeating commitments (#376) ---------------------------------------------

_SHORTHAND = {"day": "daily", "week": "weekly", "month": "monthly"}
_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MON_TO_FRI = [0, 1, 2, 3, 4]


def format_repeat(interval: Mapping[str, Any]) -> str:
    """``{"unit": "day", "every": 1}`` -> "daily"; ``every`` > 1 -> "every 2 weeks".

    A weekday set (#427) names its days: Mon-Fri every week is "weekdays",
    anything else "weekly on Mon, Wed" or "every 2 weeks on Tue, Thu".
    """
    unit, every = str(interval["unit"]), int(interval["every"])
    days = interval.get("days")
    if days:
        if every == 1 and sorted(days) == _MON_TO_FRI:
            return "weekdays"
        names = ", ".join(_WEEKDAY_NAMES[day] for day in sorted(days))
        return f"weekly on {names}" if every == 1 else f"every {every} weeks on {names}"
    if every == 1:
        return _SHORTHAND[unit]
    return f"every {every} {unit}s"


def occurs_on(series: FocusPlanEntry, day: date) -> bool:
    """Whether a commitment has an occurrence on ``day``.

    A repeating one counts from its own ``plan_date`` (its first occurrence),
    never chained from the previous occurrence, so a monthly series that starts
    on the 31st lands on the last day of a short month and back on the 31st
    after it. A weekly series with ``days`` (#427) lands on each of those
    weekdays in every ``every``-th calendar week (Monday-based) counted from the
    week ``plan_date`` falls in; ``plan_date`` itself need not be one of them.
    Skips are not consulted here; ``day_commitments`` applies them.
    """
    interval = series.repeat_interval
    if interval is None:
        return series.plan_date == day
    anchor = series.plan_date
    if day < anchor:
        return False
    unit, every = interval["unit"], int(interval["every"])
    if unit == "day":
        return (day - anchor).days % every == 0
    if unit == "week":
        days = interval.get("days")
        if days:
            anchor_monday = anchor - timedelta(days=anchor.weekday())
            day_monday = day - timedelta(days=day.weekday())
            weeks = (day_monday - anchor_monday).days // 7
            return weeks % every == 0 and day.weekday() in days
        return (day - anchor).days % (7 * every) == 0
    if unit == "month":
        months = (day.year - anchor.year) * 12 + (day.month - anchor.month)
        if months % every:
            return False
        return day.day == min(anchor.day, monthrange(day.year, day.month)[1])
    raise ValueError(f"Unknown recurrence unit: {unit!r}")


def day_commitments(db: Session, plan_date: date) -> list[FocusPlanEntry]:
    """The commitments that happen on ``plan_date``, in start order.

    That day's one-off commitments, plus every live series with an occurrence
    that day which no live skip removes. A series is one row however many days
    it covers: its occurrences are worked out here, on read, and nothing is
    stored for a day until somebody makes a decision about it.
    """
    one_offs = list(
        db.execute(
            active(FocusPlanEntry).where(
                FocusPlanEntry.plan_date == plan_date,
                FocusPlanEntry.task_id.is_(None),
                FocusPlanEntry.series_id.is_(None),
                FocusPlanEntry.repeat_interval.is_(None),
            )
        ).scalars()
    )
    series = [
        entry
        for entry in db.execute(
            active(FocusPlanEntry).where(
                FocusPlanEntry.repeat_interval.is_not(None),
                FocusPlanEntry.plan_date <= plan_date,
            )
        ).scalars()
        if occurs_on(entry, plan_date)
    ]
    skipped: set[int | None] = set()
    if series:
        skipped = set(
            db.execute(
                select(FocusPlanEntry.series_id).where(
                    FocusPlanEntry.series_id.in_([entry.id for entry in series]),
                    FocusPlanEntry.plan_date == plan_date,
                    FocusPlanEntry.deleted_at.is_(None),
                )
            ).scalars()
        )
    happening = [*one_offs, *(entry for entry in series if entry.id not in skipped)]
    return sorted(happening, key=lambda entry: (entry.start_minute or 0, entry.id))


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


def _label(db: Session, entry: FocusPlanEntry, task_title: str | None) -> str:
    when = "" if entry.start_minute is None else f" at {format_time(entry.start_minute)}"
    day = entry.plan_date.isoformat()
    if entry.series_id is not None:
        series = db.get(FocusPlanEntry, entry.series_id)
        title = series.title if series is not None else "?"
        return f'Commitment "{title}" on {day}'
    if entry.task_id is None:
        if entry.repeat_interval is not None:
            cadence = format_repeat(entry.repeat_interval)
            return f'Commitment "{entry.title}"{when}, {cadence} from {day},'
        return f'Commitment "{entry.title}"{when} on {day}'
    return f'Task "{task_title}" pinned{when} on {day}'


def _record(
    db: Session, entry: FocusPlanEntry, action: str, task: Task | None, verb: str
) -> None:
    activity.record_event(
        db,
        project_id=task.project_id if task is not None else None,
        entity_type=ENTITY_TYPE,
        entity_id=entry.id,
        action=action,
        summary=f"{_label(db, entry, task.title if task else None)} {verb}",
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
    repeat_interval: Mapping[str, Any] | None = None,
) -> FocusPlanEntry:
    """Add a fixed-time commitment that is not a task. Caller commits.

    With ``repeat_interval`` it is a series that recurs from ``plan_date`` on
    (#376): still one row, read per day by ``day_commitments``.
    """
    entry = FocusPlanEntry(
        plan_date=plan_date,
        title=title,
        start_minute=start_minute,
        duration_minutes=duration_minutes,
        position=_next_position(db, plan_date),
        repeat_interval=None if repeat_interval is None else dict(repeat_interval),
    )
    db.add(entry)
    db.flush()
    _record(db, entry, "created", None, "added to Focus")
    return entry


def _live_skip(db: Session, series_id: int, plan_date: date) -> FocusPlanEntry | None:
    return db.execute(
        active(FocusPlanEntry).where(
            FocusPlanEntry.series_id == series_id, FocusPlanEntry.plan_date == plan_date
        )
    ).scalar_one_or_none()


def skip_occurrence(
    db: Session, series: FocusPlanEntry, plan_date: date
) -> FocusPlanEntry:
    """Skip one day's occurrence of a repeating commitment (#376). Caller commits.

    Stores a skip row for that day; the series and its other days are
    untouched. Idempotent: skipping a skipped day returns the skip already
    there. Undo is ``remove_entry`` on the returned skip. Stopping the whole
    series is ``remove_entry`` on the series itself.
    """
    if series.task_id is not None or series.repeat_interval is None:
        raise FocusEntryError("Only a repeating commitment has occurrences to skip")
    if not occurs_on(series, plan_date):
        raise FocusEntryError(
            f'Commitment "{series.title}" doesn\'t happen on {plan_date.isoformat()}'
        )
    existing = _live_skip(db, series.id, plan_date)
    if existing is not None:
        return existing
    skip = FocusPlanEntry(plan_date=plan_date, series_id=series.id)
    db.add(skip)
    db.flush()
    _record(db, skip, "skipped", None, "skipped")
    return skip


def update_entry(
    db: Session, entry: FocusPlanEntry, fields: dict[str, Any]
) -> FocusPlanEntry:
    """Apply a partial edit (``start_time`` as HH:MM or null, ``title``, ``duration_minutes``).

    A commitment always keeps a time, title and duration; a pin carries only a
    time. Caller commits.
    """
    if entry.series_id is not None:
        raise FocusEntryError(
            "A skipped occurrence has nothing to edit; remove the skip to bring it back"
        )
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
        if entry.series_id is not None:
            raise FocusEntryError(f"Focus entry {entry_id} is a skip and has no order")
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
    """Soft-delete an entry. Undo with restore_entry.

    Unpins a task, drops a commitment, stops a whole repeating series (every
    day it covers, #376), or removes a skip so that occurrence happens again.
    """
    soft_delete(entry)
    db.flush()
    verb = "un-skipped" if entry.series_id is not None else "removed from Focus"
    _record(db, entry, "deleted", _task_of(db, entry), verb)


def restore_entry(db: Session, entry: FocusPlanEntry) -> FocusPlanEntry:
    """Bring back a removed entry; refused if the task has been pinned again since.

    Restoring a removed skip skips that occurrence again, unless it has been
    skipped again since.
    """
    if entry.series_id is not None and _live_skip(db, entry.series_id, entry.plan_date):
        raise FocusEntryError("That occurrence is already skipped")
    if entry.task_id is not None:
        if _live_pin(db, entry.plan_date, entry.task_id) is not None:
            raise FocusEntryError("That task is already pinned to this day")
        task = tasks_service.get_task(db, entry.task_id)
        if task is None:
            raise FocusEntryError("The pinned task is in the trash")
    restore(entry)
    db.flush()
    verb = "skipped again" if entry.series_id is not None else "restored to Focus"
    _record(db, entry, "restored", _task_of(db, entry), verb)
    return entry


def purge_for_tasks(db: Session, task_ids: Sequence[int]) -> None:
    """Remove every entry naming a task that a purge is destroying.

    Purge is the one permanent delete; an entry is meaningless without its task
    and would otherwise block the delete on its foreign key. Caller commits.
    """
    if task_ids:
        db.execute(sql_delete(FocusPlanEntry).where(FocusPlanEntry.task_id.in_(task_ids)))
