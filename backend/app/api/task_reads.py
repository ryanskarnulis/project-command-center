from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import Task
from app.schemas.tasks import TaskRead
from app.services import task_dependencies as deps_service
from app.services import task_recurrence
from app.services import tasks as tasks_service


def _rollup_update(
    rollup: tasks_service.Rollup, is_blocked: bool
) -> dict[str, object]:
    """The ``model_copy`` overrides a roll-up implies.

    ``workflow_status`` is always set to the effective status
    (``tasks.capped_status``): a blocked task's ``done`` is capped to
    ``in_progress`` so the UI never shows a task as done while it is flagged
    blocked. This applies to leaves too, not only checklist parents — a leaf
    completed while its blocker was in the trash and then re-blocked on the
    blocker's restore is stored-``done``-yet-blocked, and must read
    ``in_progress`` like every other surface (``list_tasks``,
    ``effective_statuses``). For an unblocked leaf the cap is a no-op (the
    roll-up equals its stored status). Only a task with subtasks overrides its
    estimate, so the ``estimated_minutes`` override stays parent-only.
    """
    update: dict[str, object] = {
        "has_subtasks": rollup.has_subtasks,
        "workflow_status": tasks_service.capped_status(
            rollup.workflow_status, is_blocked
        ),
    }
    if rollup.has_subtasks:
        update["estimated_minutes"] = rollup.estimated_minutes
    return update


def _blocking_update(blocked_task_count: int) -> dict[str, object]:
    return {
        "is_blocking": blocked_task_count > 0,
        "blocked_task_count": blocked_task_count,
    }


def read_with_blocked(db: Session, task: Task) -> TaskRead:
    """A single task's read model with ``is_blocked`` and roll-ups populated."""
    blocker_counts = deps_service.top_level_blocker_counts(db, [task.id])
    is_blocked = deps_service.is_blocked(db, task.id)
    return TaskRead.model_validate(task).model_copy(
        update={
            "is_blocked": is_blocked,
            "next_occurrence_date": task_recurrence.next_occurrence_date(task),
            **_blocking_update(blocker_counts.get(task.id, 0)),
            **_rollup_update(tasks_service.get_rollup(db, task), is_blocked),
        }
    )


def reads_with_blocked(db: Session, tasks: Sequence[Task]) -> list[TaskRead]:
    """A task list with ``is_blocked`` and roll-ups resolved in one query each (no N+1)."""
    task_ids = [t.id for t in tasks]
    blocked = deps_service.blocked_task_ids(db, task_ids)
    blocker_counts = deps_service.top_level_blocker_counts(db, task_ids)
    rollups = tasks_service.compute_rollups(db, tasks)
    return [
        TaskRead.model_validate(t).model_copy(
            update={
                "is_blocked": t.id in blocked,
                "next_occurrence_date": task_recurrence.next_occurrence_date(t),
                **_blocking_update(blocker_counts.get(t.id, 0)),
                **_rollup_update(rollups[t.id], t.id in blocked),
            }
        )
        for t in tasks
    ]
