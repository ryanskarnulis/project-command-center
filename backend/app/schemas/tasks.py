from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import TaskPriority, TaskWorkflowStatus
from app.schemas.common import NonBlankStr, OptionalStrippedStr, UTCDateTime

# A duration estimate, when present, must be a positive whole number of minutes.
PositiveMinutes = Annotated[int, Field(gt=0)]


class RepeatInterval(BaseModel):
    """A task recurrence cadence, e.g. ``{"unit": "week", "every": 2}``.

    Both fields are always required (no defaults), so a partial payload is a 422
    rather than a silently ``None`` cadence. ``every`` is bounded 1-12 to match
    the natural-text UI (``daily`` … ``every 12 months``).
    """

    unit: Literal["day", "week", "month"]
    every: Annotated[int, Field(ge=1, le=12)]


class TaskCreate(BaseModel):
    title: NonBlankStr
    description: OptionalStrippedStr = None
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open
    # Optional so an omitted priority is distinguishable from an explicit choice:
    # a subtask with no priority/due_date seeds them from its parent (service
    # layer), while a parent-less task still resolves to ``medium``.
    priority: TaskPriority | None = None
    due_date: date | None = None
    # Honored only by the unscoped ``POST /api/tasks`` route; the project-scoped
    # route takes the project from its path and ignores this field. Omit to file
    # in General.
    project_id: int | None = None
    parent_task_id: int | None = None
    estimated_minutes: PositiveMinutes | None = None


# ``TaskUpdate`` columns backed by NOT-NULL DB columns: an explicit ``null`` on
# any of these must be a 422, never a silent NOT-NULL violation.
_TASK_UPDATE_NON_NULLABLE_FIELDS = (
    "title",
    "priority",
    "workflow_status",
)


class TaskUpdate(BaseModel):
    title: NonBlankStr | None = None
    description: OptionalStrippedStr = None
    workflow_status: TaskWorkflowStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    # Day-plan snooze (Focus page "defer"): the scheduler skips the task while
    # this is after the plan's target date. Explicit null clears the deferral.
    deferred_until: date | None = None
    project_id: int | None = None
    parent_task_id: int | None = None
    estimated_minutes: PositiveMinutes | None = None
    # Recurrence (Sprint 9L). All three rely on the route's
    # ``model_dump(exclude_unset=True)``: an absent ``repeat_interval`` is left
    # untouched, while an explicit ``null`` clears recurrence. The
    # ``repeat_interval``-without-``due_date`` rejection lives in the service
    # layer (it needs DB state — the task may already carry a due date), not a
    # field validator here.
    repeat_interval: RepeatInterval | None = None
    edit_scope: Literal["this", "future"] = "this"

    # These columns are NOT-NULL in the DB, so an explicit ``null`` must be a 422,
    # not a 500 / invalid domain state. We can't drop the ``| None`` default
    # (that's what lets a partial PATCH *omit* the field via
    # ``model_dump(exclude_unset=True)``); instead we distinguish omit from
    # explicit null via ``model_fields_set`` — present-and-None is rejected,
    # absent is fine. (The other nullable fields above may legitimately be
    # cleared to null.) ``project_id`` is intentionally not here: its
    # explicit-null behaviour is a separate decision.
    @model_validator(mode="after")
    def _reject_null_non_nullable(self) -> "TaskUpdate":
        for name in _TASK_UPDATE_NON_NULLABLE_FIELDS:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared to null")
        return self


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    parent_task_id: int | None
    title: str
    description: str | None
    workflow_status: TaskWorkflowStatus
    priority: TaskPriority
    due_date: date | None
    deferred_until: date | None
    estimated_minutes: int | None
    repeat_interval: RepeatInterval | None
    recurrence_id: str | None
    # Derived (not stored): the due date of the next occurrence for an open
    # recurring task, so the UI can render "next <date>" beside the repeat badge.
    # None for non-recurring or done tasks; populated by the read helpers.
    next_occurrence_date: date | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime
    deleted_at: UTCDateTime | None = None
    # Derived (not stored): true while any dependency is unfinished. Defaults to
    # False so an ORM Task lacking the attribute (e.g. a freshly created task with
    # no dependencies) serializes cleanly; list/detail routes populate it.
    is_blocked: bool = False
    # Derived (not stored): true when this task is the highest unfinished blocker
    # in an active dependency chain. ``blocked_task_count`` is the transitive
    # count of unfinished downstream tasks waiting on it.
    is_blocking: bool = False
    blocked_task_count: int = 0
    # Derived (not stored): true when the task has subtasks, in which case
    # ``estimated_minutes`` and ``workflow_status`` above carry the rolled-up values
    # and are read-only in the UI. Defaults to False for the same reason as above.
    has_subtasks: bool = False


class TaskSeries(BaseModel):
    """A recurrence series: the active and skipped occurrences sharing a
    ``recurrence_id``.

    Occurrences reuse ``TaskRead`` (which already carries ``deleted_at``, so the
    client can mark skipped rows) and are ordered oldest due date first. Skipped
    rows are soft-deleted but included, so the timeline tells the truth about the
    chain; normally-trashed occurrences are excluded (they live in the trash).
    That makes ``deleted_at`` an exact stand-in for "skipped" on this payload,
    which is why ``skipped_at`` needn't be on the wire.
    """

    recurrence_id: str
    occurrences: list[TaskRead]
