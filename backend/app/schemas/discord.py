from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.common import InboxRawText


class DiscordInboxRequest(BaseModel):
    raw_text: InboxRawText


class DiscordInboxResponse(BaseModel):
    inbox_item_id: int
    summary: str | None
    project_hint: str | None
    task_titles: list[str]
    candidate_count: int
    needs_review: bool


class DiscordTaskItem(BaseModel):
    """One open task, trimmed to what the bot needs for a short list line."""

    id: int
    title: str
    project_name: str | None
    due_date: date | None


class DiscordTaskList(BaseModel):
    """Open tasks for ``/tasks``. ``total`` is the full count before any display cap."""

    tasks: list[DiscordTaskItem]
    total: int


class DiscordTaskSearchResult(BaseModel):
    """Ranked open-task candidates for ``/done`` to resolve or disambiguate."""

    tasks: list[DiscordTaskItem]
