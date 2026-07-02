from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import Task
from app.schemas.tasks import TaskRead
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service


def _rollup_update(rollup: tasks_service.Rollup) -> dict[str, object]:
    """The ``model_copy`` overrides a roll-up implies.

    Only a task with accepted subtasks overrides its estimate/status; a leaf keeps
    its stored values, so we touch nothing but the ``has_subtasks`` flag for it.
    """
    update: dict[str, object] = {"has_subtasks": rollup.has_subtasks}
    if rollup.has_subtasks:
        update["estimated_minutes"] = rollup.estimated_minutes
        update["workflow_status"] = rollup.workflow_status
    return update


def _blocking_update(blocked_task_count: int) -> dict[str, object]:
    return {
        "is_blocking": blocked_task_count > 0,
        "blocked_task_count": blocked_task_count,
    }


def read_with_blocked(db: Session, task: Task) -> TaskRead:
    """A single task's read model with ``is_blocked`` and roll-ups populated."""
    blocker_counts = deps_service.top_level_blocker_counts(db, [task.id])
    return TaskRead.model_validate(task).model_copy(
        update={
            "is_blocked": deps_service.is_blocked(db, task.id),
            **_blocking_update(blocker_counts.get(task.id, 0)),
            **_rollup_update(tasks_service.get_rollup(db, task)),
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
                **_blocking_update(blocker_counts.get(t.id, 0)),
                **_rollup_update(rollups[t.id]),
            }
        )
        for t in tasks
    ]
