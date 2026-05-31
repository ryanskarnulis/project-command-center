from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import TaskPriority


class ExtractedTask(BaseModel):
    """A single task the model proposes from raw inbox text.

    Mirrors the README task-extraction schema. ``extra="forbid"`` so that an
    unexpected key fails validation rather than being silently dropped — the
    constitution forbids best-effort parsing of model output.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str | None = None
    due_date: date | None = None  # Pydantic v2 coerces "YYYY-MM-DD" strings.
    priority: TaskPriority = TaskPriority.medium
    assignee_hint: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionOutput(BaseModel):
    """Full validated output of the ``task_extraction`` workflow."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    project_hint: str | None = None
    tasks: list[ExtractedTask]
    needs_review: bool


class ExtractionInput(BaseModel):
    """Builder for the user message handed to the model.

    The system prompt is static markdown; the per-call ``today`` is injected
    here into the user message so the model resolves relative dates against a
    date the app controls — never one the model invents.
    """

    raw_text: str
    today: date

    def to_user_content(self) -> str:
        return f"Today's date: {self.today.isoformat()}\n\nNotes:\n{self.raw_text}"
