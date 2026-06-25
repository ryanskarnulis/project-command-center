from __future__ import annotations

from pydantic import BaseModel

from app.schemas.inbox import InboxRead
from app.schemas.projects import ProjectRead
from app.schemas.tasks import TaskRead
from app.schemas.training import TrainingExampleRead


class ProjectTrashRead(ProjectRead):
    """A trashed project plus how many tasks would return if restored with it.

    ``archived_task_count`` is the set cascade-soft-deleted with the project (it
    drives the restore confirm). Only used by the trash list, so the shared
    ``ProjectRead`` stays unchanged.
    """

    archived_task_count: int = 0


class TrashRead(BaseModel):
    """Recently soft-deleted rows across the user-facing tables (Sprint 7).

    Powers the single /trash page — one fetch, restore via per-entity routes.
    """

    projects: list[ProjectTrashRead]
    tasks: list[TaskRead]
    inbox_items: list[InboxRead]
    training_examples: list[TrainingExampleRead]


class ProjectRestoreResult(BaseModel):
    """Outcome of restoring a project — the project plus how many tasks came back."""

    project: ProjectRead
    restored_task_count: int


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
