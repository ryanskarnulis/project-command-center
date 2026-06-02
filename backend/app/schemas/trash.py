from __future__ import annotations

from pydantic import BaseModel

from app.schemas.inbox import InboxRead
from app.schemas.projects import ProjectRead
from app.schemas.tasks import TaskRead


class TrashRead(BaseModel):
    """Recently soft-deleted rows across the user-facing tables (Sprint 7).

    Powers the single /trash page — one fetch, restore via per-entity routes.
    """

    projects: list[ProjectRead]
    tasks: list[TaskRead]
    inbox_items: list[InboxRead]
