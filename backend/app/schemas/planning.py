from __future__ import annotations

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
