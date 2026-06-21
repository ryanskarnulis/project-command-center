from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.db.models import TaskReviewStatus, TaskWorkflowStatus

SearchKind = Literal["project", "task", "inbox"]


class SearchResultItem(BaseModel):
    """One match, normalized across the three searched entity types.

    ``title`` is the primary display line; ``subtitle`` is an optional secondary
    line (e.g. a task's project name or an inbox item's summary). ``project_id``
    is populated for tasks so the UI can route to the owning project when needed.

    ``review_status``/``workflow_status`` are populated only for the ``task`` kind
    (``None`` for projects and inbox items). They let the command bar's ``/done``
    action offer only acceptable, not-yet-done tasks; plain search ignores them.
    """

    kind: SearchKind
    id: int
    title: str
    subtitle: str | None = None
    project_id: int | None = None
    review_status: TaskReviewStatus | None = None
    workflow_status: TaskWorkflowStatus | None = None


class SearchResults(BaseModel):
    """Matches grouped by kind. Each group is independently capped by the service."""

    projects: list[SearchResultItem]
    tasks: list[SearchResultItem]
    inbox_items: list[SearchResultItem]
