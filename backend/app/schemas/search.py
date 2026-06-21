from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SearchKind = Literal["project", "task", "inbox"]


class SearchResultItem(BaseModel):
    """One match, normalized across the three searched entity types.

    ``title`` is the primary display line; ``subtitle`` is an optional secondary
    line (e.g. a task's project name or an inbox item's summary). ``project_id``
    is populated for tasks so the UI can route to the owning project when needed.
    """

    kind: SearchKind
    id: int
    title: str
    subtitle: str | None = None
    project_id: int | None = None


class SearchResults(BaseModel):
    """Matches grouped by kind. Each group is independently capped by the service."""

    projects: list[SearchResultItem]
    tasks: list[SearchResultItem]
    inbox_items: list[SearchResultItem]
