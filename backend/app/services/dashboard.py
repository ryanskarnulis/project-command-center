from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import Project, Task, TaskReviewStatus, TaskWorkflowStatus
from app.services import tasks as tasks_service
from app.services.common import active


def _open_tasks(db: Session) -> list[Task]:
    """Active accepted tasks whose EFFECTIVE (rolled-up) status is not done.

    A checklist parent's status is derived from its children and never written
    back to its stored column (it stays "open" once created), so a SQL
    ``workflow_status != done`` count would keep a fully-completed checklist in the
    open totals indefinitely. Resolve the roll-up and count effective status
    instead — the same rule ``list_tasks(exclude_done=True)`` applies.

    One query: the fetched set is the complete active, accepted set, so the roll-up
    child map is built from it in memory rather than re-read.
    """
    tasks = (
        db.execute(
            active(Task).where(Task.review_status == TaskReviewStatus.accepted)
        )
        .scalars()
        .all()
    )
    rollups = tasks_service.compute_rollups_for_full_set(tasks)
    return [
        task
        for task in tasks
        if rollups[task.id].workflow_status != TaskWorkflowStatus.done
    ]


def _per_project_open_counts(
    db: Session, open_tasks: Sequence[Task]
) -> Sequence[tuple[Project, int]]:
    """Active projects paired with their open-task count (may be 0)."""
    counts: dict[int, int] = defaultdict(int)
    for task in open_tasks:
        if task.project_id is not None:
            counts[task.project_id] += 1

    projects = db.execute(
        active(Project)
        .where(Project.closed_at.is_(None))
        .order_by(Project.sort_order, Project.id)
    ).scalars().all()
    return [(project, counts.get(project.id, 0)) for project in projects]


def get_overview(
    db: Session,
) -> tuple[int, Sequence[tuple[Project, int]]]:
    """Return (total_open_tasks, per_project_counts).

    No model calls; both pieces come from the tasks/projects tables.
    """
    open_tasks = _open_tasks(db)
    total = len(open_tasks)
    per_project = _per_project_open_counts(db, open_tasks)
    return total, per_project
