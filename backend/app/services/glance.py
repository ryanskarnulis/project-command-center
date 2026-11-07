"""The glance (#504): a tiny read-only summary of the day, off the service layer.

No model call, no writes, no audit rows. It is capped to about 150 tokens
(~600 characters of JSON): the due-today list holds the top few, titles are
clipped, and counts stand in for the rest. Anything bigger is an ``ask``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.db.models import Task, TaskPriority
from app.schemas.glance import Glance, GlanceTaskRef
from app.services import focus_plan as focus_plan_service
from app.services import tasks as tasks_service

# Sized so the worst case (cap titles, a clipped Focus title, a three-digit
# count) still renders under 600 characters; test_agent_glance pins it.
MAX_DUE_TODAY = 4
MAX_TITLE_CHARS = 40

_PRIORITY_RANK = {
    TaskPriority.urgent: 0,
    TaskPriority.high: 1,
    TaskPriority.medium: 2,
    TaskPriority.low: 3,
}


def _clip(title: str) -> str:
    title = " ".join(title.split())
    if len(title) <= MAX_TITLE_CHARS:
        return title
    return title[: MAX_TITLE_CHARS - 1].rstrip() + "…"


def _ref(task: Task) -> GlanceTaskRef:
    return GlanceTaskRef(id=str(task.id), title=_clip(task.title))


def get_glance(db: Session, *, today: date) -> Glance:
    """Today's and overdue open tasks plus today's Focus (its first pin)."""
    # Effective (rolled-up) status, like every other open-task read.
    open_tasks = tasks_service.list_tasks(db, exclude_done=True)
    open_by_id = {task.id: task for task in open_tasks}

    due_today = sorted(
        (task for task in open_tasks if task.due_date == today),
        key=lambda task: (_PRIORITY_RANK[task.priority], task.id),
    )
    overdue = sum(1 for task in open_tasks if task.due_date and task.due_date < today)
    upcoming = [
        task.due_date for task in open_tasks if task.due_date and task.due_date >= today
    ]

    focus: Task | None = None
    for entry in focus_plan_service.list_entries(db, today):
        if entry.task_id is not None and entry.task_id in open_by_id:
            focus = open_by_id[entry.task_id]
            break

    shown = due_today[:MAX_DUE_TODAY]
    return Glance(
        as_of=datetime.now(UTC).replace(microsecond=0),
        due_today=[_ref(task) for task in shown],
        due_today_more=len(due_today) - len(shown) or None,
        overdue=overdue,
        focus=_ref(focus) if focus is not None else None,
        next_due=min(upcoming) if upcoming else None,
    )
