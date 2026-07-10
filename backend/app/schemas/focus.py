from __future__ import annotations

import enum
from datetime import date

from pydantic import BaseModel

from app.db.models import TaskPriority, TaskWorkflowStatus


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
    project_id: int | None
    start_time: str  # HH:MM, local clock time within the planned day
    end_time: str  # HH:MM
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


class OverflowTask(BaseModel):
    """A schedulable task that did not fit in the available capacity.

    Returned in ranked order so the UI shows the most important unscheduled work
    first instead of hiding it.
    """

    task_id: int
    title: str
    project_id: int | None
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
    project_id: int | None
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
    overflow: list[OverflowTask]
    blocked: list[BlockedTask]
