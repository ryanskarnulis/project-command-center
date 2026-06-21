from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from app.db.models import Task, TaskPriority, TaskReviewStatus
from app.services.common import active

# Within a single day, surface the most urgent work first. Mirrors the ranking
# used by the day scheduler (services/today._PRIORITY_RANK) so the calendar and
# /today agree on what "more important" means.
_PRIORITY_RANK = {
    TaskPriority.urgent: 0,
    TaskPriority.high: 1,
    TaskPriority.medium: 2,
    TaskPriority.low: 3,
}


def tasks_in_range(db: Session, start: date, end: date) -> Sequence[Task]:
    """Accepted, non-deleted tasks with a ``due_date`` in ``[start, end]``.

    Read-only feed for the internal calendar. Includes ``done`` tasks (so
    completed work still shows on past days) but excludes candidate/rejected
    rows — the calendar shows real, filed work, matching the main task list and
    /today semantics. Pure deterministic query; no model call.
    """
    query = (
        active(Task)
        .where(Task.review_status == TaskReviewStatus.accepted)
        .where(Task.due_date.is_not(None))
        .where(Task.due_date >= start)
        .where(Task.due_date <= end)
    )
    tasks = db.execute(query).scalars().all()
    # Order by day, then urgency, then id for a stable within-day sequence. Done
    # in Python so the priority rank matches the scheduler exactly.
    return sorted(
        tasks,
        key=lambda task: (
            task.due_date,
            _PRIORITY_RANK[task.priority],
            task.id,
        ),
    )
