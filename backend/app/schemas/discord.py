from __future__ import annotations

from pydantic import BaseModel


class DiscordInboxRequest(BaseModel):
    raw_text: str


class DiscordInboxResponse(BaseModel):
    inbox_item_id: int
    summary: str | None
    project_hint: str | None
    task_titles: list[str]
    candidate_count: int
    needs_review: bool
