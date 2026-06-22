from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency, TaskReviewStatus
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service

# One responsibility: assemble the per-project planning payload (tasks + edges).
# It does NOT compute bar geometry — that is a presentation mapping done in the
# frontend for the read-only slice. When dependency auto-shift lands (a later
# slice that mutates dates), that scheduling logic moves here, not the frontend
# (CLAUDE.md prime directive #1). Keep this thin until then.


def gantt_tasks(db: Session, project_id: int) -> Sequence[Task]:
    """Accepted, not-done tasks for the project (subtasks included)."""
    return tasks_service.list_tasks(
        db,
        project_id,
        review_status=TaskReviewStatus.accepted,
        exclude_done=True,
    )


def gantt_dependencies(
    db: Session, tasks: Sequence[Task]
) -> list[TaskDependency]:
    """Active edges between the given tasks (both endpoints in the set)."""
    return deps_service.edges_among_tasks(db, [t.id for t in tasks])
