from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from app.db.models import Task, TaskPriority, TaskReviewStatus, TaskWorkflowStatus
from app.schemas.today import (
    BlockedTask,
    BlockingTask,
    DueSignal,
    OverflowTask,
    ScheduledBlock,
    TodayPlan,
)
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service

# Unsized tasks are planned at this duration; surfaced as ``estimate_assumed`` so
# the UI never pretends the number came from the task.
DEFAULT_ESTIMATE_MINUTES = 30
DEFAULT_START_TIME = "09:00"
DEFAULT_AVAILABLE_MINUTES = 360
# A due date this many days out (or sooner) counts as "due soon".
DUE_SOON_DAYS = 3

# Smaller sorts earlier. ``in_progress`` work is pulled ahead of untouched work.
_WORKFLOW_RANK = {
    TaskWorkflowStatus.in_progress: 0,
    TaskWorkflowStatus.open: 1,
}
_DUE_RANK = {
    DueSignal.overdue: 0,
    DueSignal.due_today: 1,
    DueSignal.due_soon: 2,
    DueSignal.none: 3,
}
_PRIORITY_RANK = {
    TaskPriority.urgent: 0,
    TaskPriority.high: 1,
    TaskPriority.medium: 2,
    TaskPriority.low: 3,
}


def _due_signal(due_date: date | None, target_date: date) -> DueSignal:
    if due_date is None:
        return DueSignal.none
    if due_date < target_date:
        return DueSignal.overdue
    if due_date == target_date:
        return DueSignal.due_today
    if (due_date - target_date).days <= DUE_SOON_DAYS:
        return DueSignal.due_soon
    return DueSignal.none


def _effective_estimate(task: Task) -> tuple[int, bool]:
    """Return ``(minutes, assumed)`` — the task's estimate or the assumed default."""
    if task.estimated_minutes is not None:
        return task.estimated_minutes, False
    return DEFAULT_ESTIMATE_MINUTES, True


def _rank_key(task: Task, target_date: date) -> tuple[int, int, int, int, int]:
    """Deterministic sort key (ascending). Mirrors the v1 rules in CURRENT.md.

    Order: in-progress first, then due urgency, then priority, then shorter tasks
    as a tie-breaker, then id for a stable final ordering.
    """
    minutes, _assumed = _effective_estimate(task)
    return (
        _WORKFLOW_RANK[task.workflow_status],
        _DUE_RANK[_due_signal(task.due_date, target_date)],
        _PRIORITY_RANK[task.priority],
        minutes,
        task.id,
    )


def _reason(task: Task, target_date: date) -> str:
    """Human-readable, deterministic explanation of a task's placement."""
    parts = [task.workflow_status.value.replace("_", "-")]
    signal = _due_signal(task.due_date, target_date)
    if signal is not DueSignal.none:
        parts.append(signal.value.replace("_", " "))
    parts.append(f"{task.priority.value} priority")
    return " · ".join(parts)


def _format_time(minutes_from_midnight: int) -> str:
    hours, minutes = divmod(minutes_from_midnight, 60)
    return f"{hours:02d}:{minutes:02d}"


def _parse_time(value: str) -> int:
    """Parse ``HH:MM`` into minutes from midnight. Caller validates the format."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _pack(
    ranked: Sequence[Task],
    start_minutes: int,
    available_minutes: int,
    target_date: date,
) -> tuple[list[ScheduledBlock], list[OverflowTask], int]:
    """Place ranked tasks into sequential blocks until capacity is exhausted.

    Greedy in rank order: the first task that does not fit, and every task after
    it, become overflow (preserving ranked order). This matches "build sequential
    blocks ... until available minutes are exhausted" — it does not skip ahead to
    fit a smaller later task.
    """
    blocks: list[ScheduledBlock] = []
    overflow: list[OverflowTask] = []
    used = 0
    full = False
    for task in ranked:
        minutes, assumed = _effective_estimate(task)
        signal = _due_signal(task.due_date, target_date)
        if full or used + minutes > available_minutes:
            full = True
            overflow.append(
                OverflowTask(
                    task_id=task.id,
                    title=task.title,
                    project_id=task.project_id,
                    priority=task.priority,
                    workflow_status=task.workflow_status,
                    due_date=task.due_date,
                    due_signal=signal,
                    estimated_minutes=minutes,
                    estimate_assumed=assumed,
                )
            )
            continue
        block_start = start_minutes + used
        used += minutes
        blocks.append(
            ScheduledBlock(
                task_id=task.id,
                title=task.title,
                project_id=task.project_id,
                start_time=_format_time(block_start),
                end_time=_format_time(start_minutes + used),
                estimated_minutes=minutes,
                estimate_assumed=assumed,
                priority=task.priority,
                workflow_status=task.workflow_status,
                due_date=task.due_date,
                due_signal=signal,
                reason=_reason(task, target_date),
            )
        )
    return blocks, overflow, used


def get_today_plan(
    db: Session,
    *,
    target_date: date,
    start_time: str = DEFAULT_START_TIME,
    available_minutes: int = DEFAULT_AVAILABLE_MINUTES,
) -> TodayPlan:
    """Build the deterministic day plan. No model calls; all from existing tables.

    Source tasks are accepted, not-done top-level tasks (subtasks are excluded —
    they belong to their parent). Blocked tasks (an unfinished dependency) are
    surfaced separately and never scheduled.
    """
    source = [
        task
        for task in tasks_service.list_tasks(
            db,
            review_status=TaskReviewStatus.accepted,
            exclude_done=True,
        )
        # Subtasks are scheduled as part of their parent's work, never as their
        # own day-plan rows. They surface only nested under the parent task.
        if task.parent_task_id is None
    ]
    blocked_ids = deps_service.blocked_task_ids(db, [task.id for task in source])

    blocked: list[BlockedTask] = []
    schedulable: list[Task] = []
    for task in source:
        if task.id in blocked_ids:
            blocked.append(
                BlockedTask(
                    task_id=task.id,
                    title=task.title,
                    project_id=task.project_id,
                    priority=task.priority,
                    due_date=task.due_date,
                    blocking_tasks=_unfinished_dependencies(db, task.id),
                )
            )
        else:
            schedulable.append(task)

    ranked = sorted(schedulable, key=lambda task: _rank_key(task, target_date))
    blocks, overflow, used = _pack(
        ranked, _parse_time(start_time), available_minutes, target_date
    )

    return TodayPlan(
        date=target_date,
        start_time=start_time,
        available_minutes=available_minutes,
        used_minutes=used,
        scheduled=blocks,
        overflow=overflow,
        blocked=blocked,
    )


def _unfinished_dependencies(db: Session, task_id: int) -> list[BlockingTask]:
    """Active dependencies of ``task_id`` whose target is not yet done.

    Returns the blocker's title + workflow status (not just the id) so the UI can
    render a self-explanatory blocked row. Same ``get_task`` loop that already
    decided "unfinished" — it just keeps the row it had already loaded.
    """
    unfinished: list[BlockingTask] = []
    for dep in deps_service.list_dependencies(db, task_id):
        depended = tasks_service.get_task(db, dep.depends_on_task_id)
        if depended is not None and depended.workflow_status != TaskWorkflowStatus.done:
            unfinished.append(
                BlockingTask(
                    task_id=depended.id,
                    title=depended.title,
                    workflow_status=depended.workflow_status,
                )
            )
    return unfinished
