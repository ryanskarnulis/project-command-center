from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import TaskPriority, TaskReviewStatus, TaskWorkflowStatus
from app.schemas.common import NonBlankStr, OptionalStrippedStr

# A duration estimate, when present, must be a positive whole number of minutes.
PositiveMinutes = Annotated[int, Field(gt=0)]


class RepeatInterval(BaseModel):
    """A task recurrence cadence, e.g. ``{"unit": "week", "every": 2}``.

    Both fields are always required (no defaults): per project memory on
    required-nullable model fields, a default would let json_schema/Ollama omit
    the field and silently produce ``None``. ``every`` is bounded 1-12 to match
    the natural-text UI (``daily`` … ``every 12 months``).
    """

    unit: Literal["day", "week", "month"]
    every: Annotated[int, Field(ge=1, le=12)]


class TaskCreate(BaseModel):
    title: NonBlankStr
    description: OptionalStrippedStr = None
    review_status: TaskReviewStatus = TaskReviewStatus.accepted
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open
    priority: TaskPriority = TaskPriority.medium
    due_date: date | None = None
    parent_task_id: int | None = None
    estimated_minutes: PositiveMinutes | None = None
    assignee_hint: OptionalStrippedStr = None


class TaskUpdate(BaseModel):
    title: NonBlankStr | None = None
    description: OptionalStrippedStr = None
    review_status: TaskReviewStatus | None = None
    workflow_status: TaskWorkflowStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    project_id: int | None = None
    parent_task_id: int | None = None
    estimated_minutes: PositiveMinutes | None = None
    assignee_hint: OptionalStrippedStr = None
    # Recurrence (Sprint 9L). All three rely on the route's
    # ``model_dump(exclude_unset=True)``: an absent ``repeat_interval`` is left
    # untouched, while an explicit ``null`` clears recurrence. The
    # ``repeat_interval``-without-``due_date`` rejection lives in the service
    # layer (it needs DB state — the task may already carry a due date), not a
    # field validator here.
    repeat_interval: RepeatInterval | None = None
    edit_scope: Literal["this", "future"] = "this"


class SubtaskEdit(BaseModel):
    """Per-subtask edits applied on approve. Only set fields are applied.

    Narrower than the inbox ``ReviewEdit``: a breakdown subtask inherits its
    parent's project (no ``project_id`` override) and has no due date by default.
    """

    title: NonBlankStr | None = None
    description: OptionalStrippedStr = None
    priority: TaskPriority | None = None
    estimated_minutes: PositiveMinutes | None = None


class SubtaskDecision(BaseModel):
    """Approve or dismiss one suggested subtask from a task breakdown."""

    task_id: int
    action: Literal["approve", "dismiss"]
    edits: SubtaskEdit | None = None  # only used on approve


class BreakdownReviewRequest(BaseModel):
    decisions: list[SubtaskDecision]


class BreakdownReviewResult(BaseModel):
    approved: int
    dismissed: int
    finalized: bool
    training_example_id: int | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    inbox_item_id: int | None
    parent_task_id: int | None
    title: str
    description: str | None
    review_status: TaskReviewStatus
    workflow_status: TaskWorkflowStatus
    priority: TaskPriority
    due_date: date | None
    estimated_minutes: int | None
    repeat_interval: RepeatInterval | None
    recurrence_id: str | None
    confidence: float | None
    assignee_hint: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    # Derived (not stored): true while any dependency is unfinished. Defaults to
    # False so an ORM Task lacking the attribute (e.g. a freshly created task with
    # no dependencies) serializes cleanly; list/detail routes populate it.
    is_blocked: bool = False
