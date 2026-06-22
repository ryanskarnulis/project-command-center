from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.tasks import TaskRead


class DependencyEdge(BaseModel):
    """A flat ``task_id depends_on depends_on_task_id`` edge for the Gantt.

    Returned flat (not nested per task) so the frontend can draw finish-to-start
    links without per-task fetches. Both endpoints are guaranteed to be present in
    the payload's ``tasks`` list (see ``services/planning``).
    """

    task_id: int
    depends_on_task_id: int


class ProjectGantt(BaseModel):
    """The per-project planning payload: tasks plus the edges between them.

    Bar geometry (start/end from ``scheduled_start`` + estimate) is computed in
    the frontend for this read-only slice — the backend only gathers state.
    """

    tasks: list[TaskRead]
    dependencies: list[DependencyEdge]


class GanttProject(BaseModel):
    """A project's identity for grouping/coloring bars on the global timeline.

    Only id + name — bar colors are derived client-side from the project order,
    so no color is stored (the schema stays untouched for this read-only slice).
    """

    id: int
    name: str


class GlobalGantt(BaseModel):
    """The cross-project planning payload: every project's scheduled work at once.

    A superset of ``ProjectGantt`` — the same tasks + edges (here aggregated over
    all projects, so the edges may cross project boundaries) plus the ``projects``
    each task belongs to (via ``TaskRead.project_id``), for the grouped, colored
    layout. Bar geometry is still derived in the frontend.
    """

    tasks: list[TaskRead]
    dependencies: list[DependencyEdge]
    projects: list[GanttProject]


class WhatIfOverride(BaseModel):
    """One staged, unsaved placement change for a what-if preview.

    Either field may be omitted to keep the task's stored value; a supplied
    ``scheduled_start``/``estimated_minutes`` replaces it for the hypothetical run.
    Mirrors the two placement fields the task PATCH accepts.
    """

    task_id: int
    scheduled_start: date | None = None
    estimated_minutes: int | None = None


class WhatIfRequest(BaseModel):
    """The staged overrides to preview against a project's real schedule."""

    overrides: list[WhatIfOverride]


class WhatIfShift(BaseModel):
    """A task's previewed ``scheduled_start`` under the staged overrides."""

    task_id: int
    scheduled_start: date


class WhatIfResult(BaseModel):
    """The hypothetical schedule: every task that ends up on a different day.

    Includes both the directly-overridden tasks and the downstream dependents the
    cascade pushes. Nothing is persisted — the frontend renders these previewed
    starts over its real bars until the user commits or discards.
    """

    shifts: list[WhatIfShift]
