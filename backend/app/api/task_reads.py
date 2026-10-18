from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import Task, TaskWorkflowStatus
from app.schemas.tasks import TaskRead
from app.services import task_dependencies as deps_service
from app.services import task_recurrence
from app.services import tasks as tasks_service


def _rollup_update(rollup: tasks_service.Rollup) -> dict[str, object]:
    """The ``model_copy`` overrides a roll-up implies.

    ``workflow_status`` and ``is_blocked`` come straight off the roll-up, which
    resolves the roll-up and the dependency cap as one computation
    (``tasks._resolve_rollup``): a blocked task's ``done`` reads ``in_progress``,
    and so does every ancestor whose subtree contains such a task (issue #312),
    so the UI never shows work as done while part of it is still blocked. This
    applies to leaves too — a leaf completed while its blocker was in the trash
    and then re-blocked on the blocker's restore is stored-``done``-yet-blocked
    and reads ``in_progress`` like on every other surface (``list_tasks``,
    ``effective_statuses``). Only a task with subtasks overrides its estimate, so
    the ``estimated_minutes`` override stays parent-only.

    ``subtask_status`` — what the subtasks' effective statuses alone say,
    ``None`` for a leaf — rides along so the UI can mirror which status writes
    the server would refuse (``tasks._parent_status_write_takes_effect``) instead
    of round-tripping to a 409.
    """
    update: dict[str, object] = {
        "is_blocked": rollup.is_blocked,
        "has_subtasks": rollup.has_subtasks,
        "subtask_status": rollup.subtask_status,
        "workflow_status": rollup.workflow_status,
    }
    if rollup.has_subtasks:
        update["estimated_minutes"] = rollup.estimated_minutes
    return update


def _next_occurrence_update(
    task: Task, rollup_update: dict[str, object]
) -> dict[str, object]:
    """The ``next_occurrence_date`` override, keyed off the *effective* status.

    Reuses the status already resolved for this payload by ``_rollup_update`` — a
    checklist parent stays stored-``open`` while its roll-up reads ``done``, and
    reading the stored value made a finished recurring checklist advertise a date
    whose occurrence had already been spawned.
    """
    effective = rollup_update["workflow_status"]
    assert isinstance(effective, TaskWorkflowStatus)
    return {
        "next_occurrence_date": task_recurrence.next_occurrence_date(task, effective)
    }


def _blocking_update(blocked_task_count: int) -> dict[str, object]:
    return {
        "is_blocking": blocked_task_count > 0,
        "blocked_task_count": blocked_task_count,
    }


def read_with_blocked(db: Session, task: Task) -> TaskRead:
    """A single task's read model with ``is_blocked`` and roll-ups populated."""
    blocker_counts = deps_service.top_level_blocker_counts(db, [task.id])
    rollup_update = _rollup_update(tasks_service.get_rollup(db, task))
    top_level = tasks_service.effective_top_level_ids(db, [task])
    return TaskRead.model_validate(task).model_copy(
        update={
            "is_effective_top_level": task.id in top_level,
            **_next_occurrence_update(task, rollup_update),
            **_blocking_update(blocker_counts.get(task.id, 0)),
            **rollup_update,
        }
    )


def reads_with_blocked(db: Session, tasks: Sequence[Task]) -> list[TaskRead]:
    """A task list with ``is_blocked`` and roll-ups resolved in one pass (no N+1)."""
    task_ids = [t.id for t in tasks]
    blocker_counts = deps_service.top_level_blocker_counts(db, task_ids)
    rollups = tasks_service.compute_rollups(db, tasks)
    # One batched parent lookup for the whole page (no N+1).
    top_level = tasks_service.effective_top_level_ids(db, tasks)
    reads: list[TaskRead] = []
    for t in tasks:
        rollup_update = _rollup_update(rollups[t.id])
        reads.append(
            TaskRead.model_validate(t).model_copy(
                update={
                    "is_effective_top_level": t.id in top_level,
                    **_next_occurrence_update(t, rollup_update),
                    **_blocking_update(blocker_counts.get(t.id, 0)),
                    **rollup_update,
                }
            )
        )
    return reads
