from __future__ import annotations

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
