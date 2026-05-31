from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.db.models import InboxSource, TaskPriority


class InboxCreate(BaseModel):
    raw_text: str
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
    model_name: str | None
    created_at: datetime
    updated_at: datetime


class ReviewEdit(BaseModel):
    """Per-task edits applied on accept. Only set fields are applied."""

    title: str | None = None
    description: str | None = None
    due_date: date | None = None
    priority: TaskPriority | None = None
    assignee_hint: str | None = None


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
