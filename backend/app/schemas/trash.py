from __future__ import annotations

from pydantic import BaseModel

from app.schemas.inbox import InboxRead
from app.schemas.projects import ProjectRead
from app.schemas.tasks import TaskRead
from app.schemas.training import TrainingExampleRead


class TrashRead(BaseModel):
    """Recently soft-deleted rows across the user-facing tables (Sprint 7).

    Powers the single /trash page — one fetch, restore via per-entity routes.
    """

    projects: list[ProjectRead]
    tasks: list[TaskRead]
    inbox_items: list[InboxRead]
    training_examples: list[TrainingExampleRead]


class EmptyTrashResult(BaseModel):
    """Per-kind counts of rows permanently removed by ``DELETE /api/trash`` (9f)."""

    projects: int
    tasks: int
    inbox_items: int
    training_examples: int


class TrashCountResult(BaseModel):
    """Exact per-kind counts of rows currently in trash, for the nav badge (9f)."""

    projects: int
    tasks: int
    inbox_items: int
    training_examples: int
