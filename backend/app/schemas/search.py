from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.db.models import TaskWorkflowStatus

SearchKind = Literal["project", "task"]


class SearchResultItem(BaseModel):
    """One match, normalized across the searched entity types.

    ``title`` is the primary display line; ``subtitle`` is an optional secondary
    line (e.g. a task's project name). ``project_id`` is populated for tasks so
    the UI can route to the owning project when needed.

    ``workflow_status`` is populated only for the ``task`` kind (``None`` for
    projects). It lets the command bar's ``/done`` action offer only
    not-yet-done tasks; plain search ignores it.
    """

    kind: SearchKind
    id: int
    title: str
    subtitle: str | None = None
    project_id: int | None = None
    workflow_status: TaskWorkflowStatus | None = None


class SearchResults(BaseModel):
    """Matches grouped by kind. Each group is independently capped by the service."""

    projects: list[SearchResultItem]
    tasks: list[SearchResultItem]
