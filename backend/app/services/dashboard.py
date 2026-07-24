from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import Project, Task, TaskWorkflowStatus
from app.services import tasks as tasks_service
from app.services.common import active


def _open_tasks(db: Session) -> list[Task]:
    """Active tasks whose EFFECTIVE (rolled-up, blocked-aware) status is not done.

    Two derivations, exactly the pair ``list_tasks(exclude_done=True)`` applies,
    so the overview total and the drill-down list can't disagree:

    1. A checklist parent's status is derived from its children and never written
       back to its stored column (it stays "open" once created), so a SQL
       ``workflow_status != done`` count would keep a fully-completed checklist in
       the open totals indefinitely — resolve the roll-up instead.
    2. Cap a rolled-up ``done`` to ``in_progress`` when the task still waits on an
       unfinished dependency (``capped_status``); without this a blocked-but-done
       task (a checklist parent whose children are all done, or a leaf completed
       while its blocker was trashed) would be undercounted here yet shown as open
       by the task list.

    The active set is fetched once: the roll-up child map is built from it in
    memory rather than re-read, and blocked-ness is one extra scan over it.
    """
    from app.services import task_dependencies

    tasks = db.execute(active(Task)).scalars().all()
    rollups = tasks_service.compute_rollups_for_full_set(tasks)
    blocked = task_dependencies.blocked_task_ids(db, [task.id for task in tasks])
    return [
        task
        for task in tasks
        if tasks_service.capped_status(
            rollups[task.id].workflow_status, task.id in blocked
        )
        != TaskWorkflowStatus.done
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
