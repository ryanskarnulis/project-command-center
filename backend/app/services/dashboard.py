from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.db.models import Project, Task, TaskWorkflowStatus
from app.services import tasks as tasks_service
from app.services.common import active


class ProjectCounts(NamedTuple):
    """A lane's two totals: what is left, and what is finished."""

    project: Project
    open_count: int
    done_count: int


def _split_by_effective_status(db: Session) -> tuple[list[Task], list[Task]]:
    """Active tasks split into (not done, done) by EFFECTIVE status.

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

    Both come off one ``Rollup`` (the cap applies at every depth, so a parent
    over a blocked subtask stays open too — issue #312). The active set is
    fetched once: the roll-up child map is built from it in memory rather than
    re-read, and the dependency edges are one extra scan. The
    done half is the exact complement of the open half, so the lane progress bar
    the two feed can never show a ratio the lane header contradicts.
    """
    tasks = db.execute(active(Task)).scalars().all()
    rollups = tasks_service.compute_rollups_for_full_set(db, tasks)
    open_tasks: list[Task] = []
    done_tasks: list[Task] = []
    for task in tasks:
        effective = rollups[task.id].workflow_status
        bucket = done_tasks if effective == TaskWorkflowStatus.done else open_tasks
        bucket.append(task)
    return open_tasks, done_tasks


def _per_project_counts(
    db: Session, open_tasks: Sequence[Task], done_tasks: Sequence[Task]
) -> Sequence[ProjectCounts]:
    """Active projects paired with their open and done counts (either may be 0)."""
    open_counts: dict[int, int] = defaultdict(int)
    done_counts: dict[int, int] = defaultdict(int)
    for tasks, counts in ((open_tasks, open_counts), (done_tasks, done_counts)):
        for task in tasks:
            if task.project_id is not None:
                counts[task.project_id] += 1

    projects = db.execute(
        active(Project)
        .where(Project.closed_at.is_(None))
        .order_by(Project.sort_order, Project.id)
    ).scalars().all()
    return [
        ProjectCounts(
            project=project,
            open_count=open_counts.get(project.id, 0),
            done_count=done_counts.get(project.id, 0),
        )
        for project in projects
    ]


def open_task_counts(db: Session) -> dict[int, int]:
    """Open (not effectively done) active tasks per project id, closed projects
    included; a project with none is absent. The lanes' own numbers (#414)."""
    open_tasks, _ = _split_by_effective_status(db)
    counts: dict[int, int] = defaultdict(int)
    for task in open_tasks:
        if task.project_id is not None:
            counts[task.project_id] += 1
    return dict(counts)


def get_overview(
    db: Session,
) -> tuple[int, Sequence[ProjectCounts]]:
    """Return (total_open_tasks, per_project_counts).

    No model calls; both pieces come from the tasks/projects tables.
    """
    open_tasks, done_tasks = _split_by_effective_status(db)
    total = len(open_tasks)
    per_project = _per_project_counts(db, open_tasks, done_tasks)
    return total, per_project


class RosterEntry(NamedTuple):
    """One open lane as the agent's state block lists it (#412)."""

    project_id: int
    name: str
    open_count: int
    overdue_count: int


class AgentSnapshot(NamedTuple):
    """What the agent is shown at the start of a run (#412): the open lanes
    with their counts, and today's totals over every open task."""

    roster: Sequence[RosterEntry]
    overdue: int
    due_today: int
    blocked: int


def get_agent_snapshot(db: Session, today: date) -> AgentSnapshot:
    """The agent's state-block numbers, by EFFECTIVE status (#412).

    The same open set as :func:`get_overview` (one roll-up pass), so a lane's
    open count here is the one the dashboard shows. Overdue is open with a due
    date before ``today``; blocked is open and waiting on an unfinished
    dependency. No model calls, no writes.
    """
    tasks = db.execute(active(Task)).scalars().all()
    rollups = tasks_service.compute_rollups_for_full_set(db, tasks)
    open_tasks = [
        task for task in tasks if rollups[task.id].workflow_status != TaskWorkflowStatus.done
    ]
    overdue = [
        task for task in open_tasks if task.due_date is not None and task.due_date < today
    ]
    overdue_counts: dict[int, int] = defaultdict(int)
    for task in overdue:
        if task.project_id is not None:
            overdue_counts[task.project_id] += 1
    per_project = _per_project_counts(db, open_tasks, [])
    return AgentSnapshot(
        roster=[
            RosterEntry(
                project_id=counts.project.id,
                name=counts.project.name,
                open_count=counts.open_count,
                overdue_count=overdue_counts.get(counts.project.id, 0),
            )
            for counts in per_project
        ],
        overdue=len(overdue),
        due_today=sum(1 for task in open_tasks if task.due_date == today),
        blocked=sum(1 for task in open_tasks if rollups[task.id].is_blocked),
    )
