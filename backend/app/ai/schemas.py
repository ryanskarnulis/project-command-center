from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import TaskPriority, TaskStatus


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


class ProjectChoice(BaseModel):
    """One project offered to the matcher, with the names it goes by."""

    id: int
    name: str
    aliases: list[str] = []


class MatchOutput(BaseModel):
    """Validated output of the ``project_matching`` workflow.

    ``project_id`` is the model's pick. Python still guards it: a value that is
    not one of the offered project ids is rejected and treated as no match — the
    model never gets to invent an id (prime directive #1).

    ``project_id`` is intentionally required (nullable, but no default): a default
    would mark it optional in the generated JSON schema, and the structured-output
    model then *omits* the field and silently "matches nothing". Required forces
    the model to commit to an id or an explicit ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int | None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None


class MatchInput(BaseModel):
    """Builder for the user message handed to the project matcher.

    The note's ``project_hint`` is the primary signal; the summary and candidate
    task titles are extra context. The offered ``projects`` carry their ids so
    the model answers with an id, not a free-text name we'd have to re-resolve.
    """

    project_hint: str
    summary: str | None = None
    task_titles: list[str] = []
    projects: list[ProjectChoice]

    def to_user_content(self) -> str:
        lines = [f"Project hint: {self.project_hint}"]
        if self.summary:
            lines.append(f"Summary: {self.summary}")
        if self.task_titles:
            joined = "; ".join(self.task_titles)
            lines.append(f"Task titles: {joined}")
        lines.append("\nCandidate projects (choose one id, or null):")
        for choice in self.projects:
            aliases = f" (aliases: {', '.join(choice.aliases)})" if choice.aliases else ""
            lines.append(f"- id={choice.id}: {choice.name}{aliases}")
        return "\n".join(lines)


class SummaryTaskRow(BaseModel):
    """One task row passed to the summarizer (read-only snapshot)."""

    title: str
    status: TaskStatus
    priority: TaskPriority
    due_date: date | None = None


class SummaryInput(BaseModel):
    """Builder for the user message handed to the summary workflow.

    The profile is ``response_mode: text`` so there is no output schema —
    the model returns free-form prose. ``today`` is injected here (not left
    to the model to guess) so overdue reasoning is accurate.
    """

    project_name: str
    tasks: list[SummaryTaskRow]
    today: date

    def to_user_content(self) -> str:
        lines = [f"Project: {self.project_name}", f"Today: {self.today.isoformat()}", ""]
        if not self.tasks:
            lines.append("No open tasks.")
        else:
            lines.append("Open tasks:")
            for t in self.tasks:
                due = f", due {t.due_date}" if t.due_date else ""
                lines.append(f"  - [{t.priority}] {t.title}{due}")
        return "\n".join(lines)
