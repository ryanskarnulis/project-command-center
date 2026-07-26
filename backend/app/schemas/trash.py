from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import MutationModel
from app.schemas.projects import ProjectRead
from app.schemas.tasks import TaskRead


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


class ProjectRestoreResult(BaseModel):
    """Outcome of restoring a project — the project plus how many tasks came back."""

    project: ProjectRead
    restored_task_count: int


class PurgeSelectedRequest(MutationModel):
    """Which trashed rows ``POST /api/trash/purge`` should permanently remove.

    Ids not in trash are skipped rather than rejected: purging a parent task takes
    its subtree with it, so a child selected alongside its parent is legitimately
    gone by the time the server reaches it (BUG-11).
    """

    project_ids: list[int] = []
    task_ids: list[int] = []


class EmptyTrashResult(BaseModel):
    """Per-kind counts of rows permanently removed by ``DELETE /api/trash`` (9f).

    Also the response for ``POST /api/trash/purge`` — same shape, same meaning.
    """

    projects: int
    tasks: int


class TrashCountResult(BaseModel):
    """Exact trash counts for the nav badge and the Empty-trash confirm (9f).

    ``projects`` / ``tasks`` drive the badge and section headings (soft-deleted
    projects; *standalone* soft-deleted tasks). ``purge_total`` is the exact
    number of rows ``DELETE /api/trash`` would remove — including tasks archived
    with deleted projects and excluding protected projects — so the Empty-trash
    confirm names a figure that matches the actual deletion.
    """

    projects: int
    tasks: int
    purge_total: int
