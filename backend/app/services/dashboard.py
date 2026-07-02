from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import InboxItem, Project, Task, TaskReviewStatus, TaskWorkflowStatus
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

    projects = db.execute(active(Project).order_by(Project.id)).scalars().all()
    return [(project, counts.get(project.id, 0)) for project in projects]


def _resolved_project_ids(
    db: Session, items: Sequence[InboxItem]
) -> dict[int, int | None]:
    """Resolve recent inbox rows in batches.

    Accepted reviewed tasks are the source of truth. Items without accepted
    tasks fall back to an active suggested project, if one still exists.
    """
    inbox_ids = [item.id for item in items]
    if not inbox_ids:
        return {}

    accepted_rows = db.execute(
        select(
            Task.inbox_item_id,
            Task.project_id,
            func.count(Task.id).label("task_count"),
            func.min(Task.id).label("first_task_id"),
        )
        .where(
            Task.deleted_at.is_(None),
            Task.inbox_item_id.in_(inbox_ids),
            Task.review_status == TaskReviewStatus.accepted,
            Task.project_id.is_not(None),
        )
        .group_by(Task.inbox_item_id, Task.project_id)
    ).all()

    accepted_by_inbox: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for inbox_item_id, project_id, task_count, first_task_id in accepted_rows:
        if inbox_item_id is None or project_id is None or first_task_id is None:
            continue
        accepted_by_inbox[int(inbox_item_id)].append(
            (int(project_id), int(task_count), int(first_task_id))
        )

    resolved: dict[int, int | None] = {}
    for inbox_item_id, project_counts in accepted_by_inbox.items():
        project_id, _count, _first_task_id = min(
            project_counts, key=lambda row: (-row[1], row[2])
        )
        resolved[inbox_item_id] = project_id

    suggested_ids = {
        item.suggested_project_id
        for item in items
        if item.id not in resolved and item.suggested_project_id is not None
    }
    active_suggested_ids: set[int] = set()
    if suggested_ids:
        active_suggested_ids = set(
            db.execute(
                select(Project.id).where(
                    Project.deleted_at.is_(None),
                    Project.id.in_(suggested_ids),
                )
            )
            .scalars()
            .all()
        )

    for item in items:
        if item.id in resolved:
            continue
        suggested_project_id = item.suggested_project_id
        if suggested_project_id is not None and suggested_project_id in active_suggested_ids:
            resolved[item.id] = suggested_project_id
        else:
            resolved[item.id] = None
    return resolved


def _recent_inbox_items(
    db: Session, limit: int = 10
) -> Sequence[tuple[InboxItem, int | None]]:
    items = (
        db.execute(active(InboxItem).order_by(InboxItem.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    resolved_ids = _resolved_project_ids(db, items)
    return [(item, resolved_ids[item.id]) for item in items]


def get_overview(
    db: Session,
) -> tuple[int, Sequence[tuple[Project, int]], Sequence[tuple[InboxItem, int | None]]]:
    """Return (total_open_tasks, per_project_counts, recent_inbox_items).

    No model calls. All three pieces come from existing tables.
    recent_inbox_items is a sequence of (InboxItem, resolved_project_id) pairs.
    """
    open_tasks = _open_tasks(db)
    total = len(open_tasks)
    per_project = _per_project_open_counts(db, open_tasks)
    recent = _recent_inbox_items(db)
    return total, per_project, recent
