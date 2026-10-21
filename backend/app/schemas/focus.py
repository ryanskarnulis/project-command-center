from __future__ import annotations

import enum
from datetime import date
from typing import Annotated

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from app.db.models import TaskPriority, TaskWorkflowStatus
from app.schemas.common import EntityId, MutationModel, NonBlankStr, UTCDateTime
from app.schemas.tasks import RepeatInterval

# HH:MM, 24-hour clock — the one shape a Focus time of day takes at the boundary
# (the plan query, the agent tool and the stored-plan writes alike).
TIME_OF_DAY_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
TimeOfDay = Annotated[str, Field(pattern=TIME_OF_DAY_PATTERN)]
# A commitment lasts at least five minutes and at most a whole day.
CommitmentMinutes = Annotated[int, Field(ge=5, le=1440)]
CommitmentTitle = Annotated[NonBlankStr, Field(max_length=200)]
# A day of the week, Monday 0 to Sunday 6 (Python's ``date.weekday()``).
Weekday = Annotated[int, Field(ge=0, le=6)]


class CommitmentRepeatInterval(RepeatInterval):
    """A commitment series' cadence: the task shape plus an optional weekday set (#427).

    ``{"unit": "week", "every": 1, "days": [0, 1, 2, 3, 4]}`` is a Mon-Fri
    standup; ``every: 2`` with ``days`` is "every other week on those days".
    ``days`` is only meaningful with ``unit: "week"``; without it a weekly series
    lands on its first day's weekday, as before. Task recurrence keeps the plain
    ``RepeatInterval``: this subclass is commitments-only.
    """

    days: list[Weekday] | None = Field(default=None, min_length=1, max_length=7)

    @model_validator(mode="after")
    def _check_days(self) -> CommitmentRepeatInterval:
        if self.days is None:
            return self
        if self.unit != "week":
            raise ValueError('days applies only to unit "week"')
        if len(set(self.days)) != len(self.days):
            raise ValueError("days must not repeat a weekday")
        self.days = sorted(self.days)
        return self

    @model_serializer(mode="wrap")
    def _omit_absent_days(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        # Without a weekday set the cadence keeps the plain task shape, both as
        # stored on the series row and in every response.
        data: dict[str, object] = handler(self)
        if data.get("days") is None:
            data.pop("days", None)
        return data


class DueSignal(enum.StrEnum):
    """How a task's due date relates to the plan's target day.

    Derived in the scheduler (no stored column): ``overdue`` is past, ``due_today``
    is the target day, ``due_soon`` is within the look-ahead window, ``none`` has no
    due date.
    """

    overdue = "overdue"
    due_today = "due_today"
    due_soon = "due_soon"
    none = "none"


class ScheduledBlock(BaseModel):
    """A task placed into a concrete time slot in the day's timeline."""

    task_id: int
    title: str
    project_id: int
    start_time: str  # HH:MM, local clock time
    end_time: str  # HH:MM, local clock time
    # Whole local calendar days after FocusPlan.date. Fixed-duration sessions
    # may cross midnight, but the 1440-minute capacity cap keeps these at 0 or 1.
    start_day_offset: int
    end_day_offset: int
    estimated_minutes: int
    # True when the duration was assumed (task had no estimate) rather than taken
    # from the task — the UI marks these so it doesn't pretend the task is sized.
    estimate_assumed: bool
    priority: TaskPriority
    workflow_status: TaskWorkflowStatus
    due_date: date | None
    due_signal: DueSignal
    # True when the underlying task recurs, so the row can offer "skip this
    # occurrence" alongside mark-done/defer (a non-recurring task can't be skipped).
    is_recurring: bool
    # Deterministic, human-readable explanation of why this task ranked here,
    # e.g. "in-progress · overdue · high priority". No model prose.
    reason: str
    # Set when this block is a subtask standing in for a parent that didn't fit
    # the remaining capacity — the UI labels it "part of <parent_title>". Both
    # null for a normally scheduled top-level task.
    parent_task_id: int | None = None
    parent_title: str | None = None
    # The stored day plan (#319). ``entry_id`` names the pin this block came
    # from; ``pinned`` is true for any pinned task (floating or timed), ``fixed``
    # only for a timed one — a fixed block sits at its own time and never moves.
    entry_id: int | None = None
    pinned: bool = False
    fixed: bool = False
    # A floating pin's place among the day's floating pins, 0 first (#375).
    # First-fit packing can put a later pin earlier on the clock, so this, not
    # the start time, is the order a reorder edits. Null for every other block.
    pin_order: int | None = None


class CommitmentBlock(BaseModel):
    """A fixed-time commitment that is not a task (a meeting), placed on the day."""

    entry_id: int
    title: str
    start_time: str  # HH:MM, local clock time
    end_time: str  # HH:MM, local clock time
    start_day_offset: int
    end_day_offset: int
    duration_minutes: int
    # Set when the commitment repeats (#376). ``entry_id`` is then the series
    # row: editing or removing it changes every occurrence, and
    # ``POST /focus/entries/{entry_id}/skip`` skips just this day's.
    repeat_interval: CommitmentRepeatInterval | None = None
    # A series' first day and its end (#428): the series occurs from
    # ``series_start_date`` up to the day before ``stop_date`` (null = no end).
    # Both null on a one-off. ``PUT /focus/entries/{entry_id}/stop`` moves the
    # end; a stop has to fall after the first day.
    series_start_date: date | None = None
    stop_date: date | None = None
    # Set when this day's occurrence has been edited for that day only (#428):
    # the block's title, time and length are the override's, ``entry_id`` is
    # still the series, and ``DELETE /focus/entries/{override_entry_id}``
    # resets the day to the usual occurrence.
    override_entry_id: int | None = None


class SkippedCommitmentBlock(CommitmentBlock):
    """A repeating commitment's occurrence that has been skipped for the day (#429).

    Listed apart from ``FocusPlan.commitments`` so nothing reading the plan
    mistakes it for time that is taken: it holds no time and blocks nothing.
    ``entry_id`` is the series; ``skip_entry_id`` is the skip row, and
    ``DELETE /focus/entries/{skip_entry_id}`` un-skips the occurrence (its undo
    is ``POST /focus/entries/{skip_entry_id}/restore``).
    """

    # Required, no default: always emitted (a skipped block without it is useless).
    skip_entry_id: int


class OverflowTask(BaseModel):
    """A schedulable task that did not fit in the available capacity.

    Returned in ranked order so the UI shows the most important unscheduled work
    first instead of hiding it.
    """

    task_id: int
    title: str
    project_id: int
    priority: TaskPriority
    workflow_status: TaskWorkflowStatus
    due_date: date | None
    due_signal: DueSignal
    # True when the underlying task recurs — enables "skip this occurrence" on the
    # overflow row.
    is_recurring: bool
    estimated_minutes: int
    estimate_assumed: bool
    # How many of this task's subtasks made the timeline in its place. Non-zero
    # means the parent overflowed but part of its work is still scheduled.
    scheduled_subtask_count: int = 0


class BlockingTask(BaseModel):
    """An unfinished dependency that is keeping a blocked task off the schedule.

    Carries the blocker's title and workflow status so the UI can show *what* the
    task is waiting on (and how close it is to done) without a second fetch per id.
    """

    task_id: int
    title: str
    workflow_status: TaskWorkflowStatus


class BlockedTask(BaseModel):
    """A task kept out of the schedule because a dependency is unfinished."""

    task_id: int
    title: str
    project_id: int
    priority: TaskPriority
    due_date: date | None
    # Active dependencies that are not yet done — what the UI warns about. Each
    # carries title + workflow status so a blocked row is self-explanatory.
    blocking_tasks: list[BlockingTask]


class FocusPlan(BaseModel):
    """The full deterministic plan for a single day."""

    date: date
    start_time: str  # HH:MM
    available_minutes: int
    used_minutes: int
    scheduled: list[ScheduledBlock]
    # Fixed-time commitments that fall in the plan, in start order. Kept apart
    # from ``scheduled`` so every scheduled block is still a task.
    commitments: list[CommitmentBlock] = []
    # Occurrences of repeating commitments skipped for this day (#429), in start
    # order. Not part of the day: shown so a skip can be taken back later.
    skipped_commitments: list[SkippedCommitmentBlock] = []
    overflow: list[OverflowTask]
    blocked: list[BlockedTask]


class FocusPinCreate(MutationModel):
    """Pin a task into a day's plan; with ``start_time`` it becomes a fixed block."""

    plan_date: date
    task_id: EntityId
    start_time: TimeOfDay | None = None


class FocusCommitmentCreate(MutationModel):
    """Add a fixed-time commitment that is not a task, e.g. a meeting."""

    plan_date: date
    title: CommitmentTitle
    start_time: TimeOfDay
    duration_minutes: CommitmentMinutes
    # Repeat from ``plan_date`` on, in the task cadence shape (#376) plus an
    # optional weekday set (#427). Occurrences are read per day, never stored
    # ahead of time.
    repeat_interval: CommitmentRepeatInterval | None = None


class FocusOccurrenceSkip(MutationModel):
    """Skip one day's occurrence of a repeating commitment (#376)."""

    plan_date: date


class FocusSeriesStop(MutationModel):
    """End a repeating commitment from a day on, or take the end away (#428).

    ``stop_date`` is the first day the series no longer occurs; the days before
    it keep their occurrences. ``null`` lets it repeat for good again. Required
    (no default): an empty body is a mistake, not "no end".
    """

    stop_date: date | None


class FocusOccurrenceEdit(MutationModel):
    """Change one day's occurrence of a repeating commitment, that day only (#428).

    The fields left out keep the series' values as they stand now. Editing a
    day already edited changes that edit.
    """

    plan_date: date
    start_time: TimeOfDay | None = None
    title: CommitmentTitle | None = None
    duration_minutes: CommitmentMinutes | None = None


class FocusEntryUpdate(MutationModel):
    """Partial edit of a stored entry; only the fields present are touched.

    ``start_time: null`` floats a timed pin again (a commitment always keeps a
    time); ``title`` and ``duration_minutes`` apply to commitments only.
    """

    start_time: TimeOfDay | None = None
    title: CommitmentTitle | None = None
    duration_minutes: CommitmentMinutes | None = None


class FocusPinOrder(MutationModel):
    """A new order for some of a day's pins (#375): ``entry_ids`` first to last.

    The named pins trade the slots they hold; pins left out keep their place.
    """

    plan_date: date
    entry_ids: list[EntityId] = Field(min_length=1, max_length=500)


class FocusEntryRead(BaseModel):
    """A stored entry as written; ``start_time`` is HH:MM or null (floating)."""

    id: int
    plan_date: date
    task_id: int | None
    title: str | None
    start_time: str | None
    duration_minutes: int | None
    position: int
    # A repeating commitment's cadence (#376); null on every other kind.
    repeat_interval: CommitmentRepeatInterval | None = None
    # A series' end (#428): the first day it no longer occurs; null = no end.
    stop_date: date | None = None
    # Set only on a skip or an override: the series whose ``plan_date``
    # occurrence it skips or edits.
    series_id: int | None = None
    created_at: UTCDateTime
    deleted_at: UTCDateTime | None


# --- The session log and running clock (#374) --------------------------------

# A booked block is at most a week of minutes: a clock left going overnight still
# books what it read. The clock's elapsed time has the same ceiling, in ms.
_MAX_BLOCK_MINUTES = 7 * 1440
_MAX_CLOCK_MS = _MAX_BLOCK_MINUTES * 60_000


class FocusClockState(enum.StrEnum):
    running = "running"
    paused = "paused"


class FocusClockSet(MutationModel):
    """Put the one live clock in a state: start, pause, resume or an undo's restore.

    ``elapsed_ms`` is the time already on the block as the client sees it. The
    server stamps "now" itself, so two devices' clocks never have to agree: a
    running clock is banked ``elapsed_ms`` plus a leg starting at the server's now.
    """

    plan_date: date
    task_id: EntityId
    state: FocusClockState
    elapsed_ms: int = Field(ge=0, le=_MAX_CLOCK_MS)


class FocusClockRead(BaseModel):
    """The live clock. ``elapsed_ms`` is as of the response, measured server-side."""

    id: int
    plan_date: date
    task_id: int
    state: FocusClockState
    elapsed_ms: int
    leg_started_at: UTCDateTime | None


class FocusBlockFinish(MutationModel):
    """Book a finished block into the day's session log."""

    minutes: int = Field(ge=0, le=_MAX_BLOCK_MINUTES)
    planned_minutes: int = Field(ge=0, le=_MAX_BLOCK_MINUTES)


class FocusFinishedBlockRead(BaseModel):
    id: int
    plan_date: date
    task_id: int
    title: str
    minutes: int
    planned_minutes: int
    finished_at: UTCDateTime


class FocusSessionLogRead(BaseModel):
    """A day's finished blocks, in the order they were finished."""

    plan_date: date
    finished: list[FocusFinishedBlockRead]
