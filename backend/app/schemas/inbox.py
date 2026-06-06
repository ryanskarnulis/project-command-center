from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.db.models import InboxSource, TaskPriority
from app.schemas.common import NonBlankStr, OptionalStrippedStr


class InboxCreate(BaseModel):
    raw_text: NonBlankStr
    source: InboxSource = InboxSource.web


class InboxRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_text: str
    input_hash: str
    source: InboxSource
    summary: str | None
    project_hint: str | None
    needs_review: bool
    processed_at: datetime | None
    reviewed_at: datetime | None
    model_name: str | None
    suggested_project_id: int | None
    created_at: datetime
    updated_at: datetime


class ReviewEdit(BaseModel):
    """Per-task edits applied on accept. Only set fields are applied.

    ``project_id`` overrides the project the note was matched to: omit it to
    inherit the inbox item's suggestion or the General fallback, send an id to
    redirect the task, or send ``null`` to file it under General.
    """

    title: NonBlankStr | None = None
    description: OptionalStrippedStr = None
    due_date: date | None = None
    priority: TaskPriority | None = None
    assignee_hint: OptionalStrippedStr = None
    project_id: int | None = None


class ReviewDecision(BaseModel):
    task_id: int
    action: Literal["accept", "reject"]
    edits: ReviewEdit | None = None  # ignored on reject


class ReviewRequest(BaseModel):
    decisions: list[ReviewDecision]


class ReviewResult(BaseModel):
    accepted: int
    rejected: int
    training_example_id: int
    match_training_example_id: int | None = None


class CandidateDecision(BaseModel):
    """Per-candidate approve/dismiss for the new one-at-a-time review flow."""

    action: Literal["approve", "dismiss"]
    edits: ReviewEdit | None = None  # only used on approve


class CandidateResult(BaseModel):
    """Result of a single candidate decision."""

    task_id: int
    action: Literal["approved", "dismissed"]
    finalized: bool
    training_example_id: int | None = None
    match_training_example_id: int | None = None
