from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from sqlalchemy.orm import Session

from app.db.models import Task, TaskPriority, TaskWorkflowStatus
from app.schemas.focus import (
    BlockedTask,
    BlockingTask,
    DueSignal,
    OverflowTask,
    ScheduledBlock,
    FocusPlan,
)
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service
from app.services.tasks import Rollup

# A checklist parent's status and estimate are derived from its children and never
# written back (see ``tasks.compute_rollups``), so every read of those two fields
# below goes through this map rather than the ORM row. It covers the source tasks
# *and* their subtasks, so the ``_pack`` fallback resolves from it too.
Rollups = Mapping[int, Rollup]

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


def _effective_estimate(task: Task, rollups: Rollups) -> tuple[int, bool]:
    """Return ``(minutes, assumed)`` — the task's estimate or the assumed default.

    A checklist parent's estimate is the sum of its subtree; a leaf's roll-up
    carries its own stored value, so both cases resolve the same way.
    """
    minutes = rollups[task.id].estimated_minutes
    if minutes is not None:
        return minutes, False
    return DEFAULT_ESTIMATE_MINUTES, True


def _effective_status(task: Task, rollups: Rollups) -> TaskWorkflowStatus:
    """The rolled-up status — what the API reports, not the stored column."""
    return rollups[task.id].workflow_status


def _rank_key(
    task: Task, target_date: date, rollups: Rollups
) -> tuple[int, int, int, int, int]:
    """Deterministic sort key (ascending). Mirrors the v1 rules in CURRENT.md.

    Order: in-progress first, then due urgency, then priority, then shorter tasks
    as a tie-breaker, then id for a stable final ordering.
    """
    minutes, _assumed = _effective_estimate(task, rollups)
    return (
        _WORKFLOW_RANK[_effective_status(task, rollups)],
        _DUE_RANK[_due_signal(task.due_date, target_date)],
        _PRIORITY_RANK[task.priority],
        minutes,
        task.id,
    )


def _reason(task: Task, target_date: date, rollups: Rollups) -> str:
    """Human-readable, deterministic explanation of a task's placement."""
    parts = [_effective_status(task, rollups).value.replace("_", "-")]
    signal = _due_signal(task.due_date, target_date)
    if signal is not DueSignal.none:
        parts.append(signal.value.replace("_", " "))
    parts.append(f"{task.priority.value} priority")
    return " · ".join(parts)


def _is_deferred(task: Task, target_date: date) -> bool:
    """True while the task's day-plan snooze is still in the future."""
    return task.deferred_until is not None and task.deferred_until > target_date


def _format_time(minutes_from_midnight: int) -> tuple[str, int]:
    """Return a valid local clock time and its day offset from the plan date."""
    day_offset, clock_minutes = divmod(minutes_from_midnight, 24 * 60)
    hours, minutes = divmod(clock_minutes, 60)
    return f"{hours:02d}:{minutes:02d}", day_offset


def _parse_time(value: str) -> int:
    """Parse ``HH:MM`` into minutes from midnight. Caller validates the format."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _schedulable_subtasks(
    db: Session,
    parent: Task,
    target_date: date,
    rollups: Rollups,
    blocked_ids: set[int],
) -> list[Task]:
    """The parent's subtasks that could stand in for it on the timeline.

    Not done, not deferred, not blocked — the same eligibility the parent itself
    had. Done-ness is the rolled-up one, so a nested checklist whose own children
    are all finished isn't offered as a stand-in; the ``blocked_ids`` filter is
    what keeps a subtask with its own unfinished dependency off the timeline,
    matching the main path's guarantee. ``list_subtasks`` already orders by id.
    """
    return [
        sub
        for sub in tasks_service.list_subtasks(db, parent.id)
        if _effective_status(sub, rollups) != TaskWorkflowStatus.done
        and not _is_deferred(sub, target_date)
        and sub.id not in blocked_ids
    ]


def _pack(
    db: Session,
    ranked: Sequence[Task],
    start_minutes: int,
    available_minutes: int,
    target_date: date,
    rollups: Rollups,
    blocked_ids: set[int],
) -> tuple[list[ScheduledBlock], list[OverflowTask], int]:
    """Place ranked tasks into sequential blocks, backfilling smaller tasks.

    Greedy in rank order: each task that fits the remaining capacity is scheduled;
    a task too large for what's left is sent to overflow and scanning continues, so
    smaller lower-ranked tasks still fill the day instead of leaving it empty behind
    one oversized high-rank item. Before overflowing a too-large parent, its open
    subtasks are tried in its rank slot — each one that fits is scheduled as its own
    block (labelled with the parent), so a big task still makes partial progress.
    Both scheduled blocks and overflow preserve ranked order, and scheduled blocks
    remain sequential with no gaps.
    """
    blocks: list[ScheduledBlock] = []
    overflow: list[OverflowTask] = []
    used = 0

    def _schedule(
        task: Task, minutes: int, assumed: bool, parent: Task | None
    ) -> None:
        nonlocal used
        block_start = start_minutes + used
        used += minutes
        reason = _reason(task, target_date, rollups)
        if parent is not None:
            reason = f"part of {parent.title} · {reason}"
        block_start_time, start_day_offset = _format_time(block_start)
        block_end_time, end_day_offset = _format_time(start_minutes + used)
        blocks.append(
            ScheduledBlock(
                task_id=task.id,
                title=task.title,
                project_id=task.project_id,
                start_time=block_start_time,
                end_time=block_end_time,
                start_day_offset=start_day_offset,
                end_day_offset=end_day_offset,
                estimated_minutes=minutes,
                estimate_assumed=assumed,
                priority=task.priority,
                workflow_status=_effective_status(task, rollups),
                due_date=task.due_date,
                due_signal=_due_signal(task.due_date, target_date),
                is_recurring=task.repeat_interval is not None,
                reason=reason,
                parent_task_id=parent.id if parent is not None else None,
                parent_title=parent.title if parent is not None else None,
            )
        )

    for task in ranked:
        minutes, assumed = _effective_estimate(task, rollups)
        if used + minutes <= available_minutes:
            _schedule(task, minutes, assumed, parent=None)
            continue
        # Too large for what's left: try the parent's own subtasks in this rank
        # slot before overflowing it, so part of the work still lands today.
        scheduled_subtasks = 0
        for sub in _schedulable_subtasks(db, task, target_date, rollups, blocked_ids):
            sub_minutes, sub_assumed = _effective_estimate(sub, rollups)
            if used + sub_minutes <= available_minutes:
                _schedule(sub, sub_minutes, sub_assumed, parent=task)
                scheduled_subtasks += 1
        overflow.append(
            OverflowTask(
                task_id=task.id,
                title=task.title,
                project_id=task.project_id,
                priority=task.priority,
                workflow_status=_effective_status(task, rollups),
                due_date=task.due_date,
                due_signal=_due_signal(task.due_date, target_date),
                is_recurring=task.repeat_interval is not None,
                estimated_minutes=minutes,
                estimate_assumed=assumed,
                scheduled_subtask_count=scheduled_subtasks,
            )
        )
    return blocks, overflow, used


def get_focus_plan(
    db: Session,
    *,
    target_date: date,
    start_time: str = DEFAULT_START_TIME,
    available_minutes: int = DEFAULT_AVAILABLE_MINUTES,
) -> FocusPlan:
    """Build the deterministic day plan. No model calls; all from existing tables.

    Source tasks are not-done top-level tasks (subtasks are excluded — they
    belong to their parent). Blocked tasks (an unfinished dependency) are
    surfaced separately and never scheduled.
    """
    source = [
        task
        for task in tasks_service.list_tasks(db, exclude_done=True)
        # Subtasks are scheduled as part of their parent's work, never as their
        # own day-plan rows (except as stand-ins when the parent doesn't fit —
        # see _pack). Deferred tasks are snoozed out of the plan entirely.
        if task.parent_task_id is None and not _is_deferred(task, target_date)
    ]
    # Resolved once, for the source tasks and their subtasks together: ranking,
    # reasons, packing and the subtask fallback all read status/estimate from here
    # so the plan agrees with what the task detail page shows.
    rollups = tasks_service.compute_subtree_rollups(db, source)
    # Blocked-ness is resolved over the whole subtree, not just top-level source,
    # so the subtask-fallback in _pack can exclude a blocked stand-in. Source ids
    # are a subset, so the top-level split below reads the same set.
    blocked_ids = deps_service.blocked_task_ids(db, list(rollups.keys()))

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

    ranked = sorted(
        schedulable, key=lambda task: _rank_key(task, target_date, rollups)
    )
    blocks, overflow, used = _pack(
        db,
        ranked,
        _parse_time(start_time),
        available_minutes,
        target_date,
        rollups,
        blocked_ids,
    )

    return FocusPlan(
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
    render a self-explanatory blocked row. The status reported is the *effective*
    one (``deps_service.effective_statuses``), matching both the decision that the
    blocker is unfinished and the status the task detail page shows.
    """
    edges = deps_service.list_dependencies(db, task_id)
    statuses = deps_service.effective_statuses(
        db, (dep.depends_on_task_id for dep in edges)
    )
    unfinished: list[BlockingTask] = []
    for dep in edges:
        status = statuses.get(dep.depends_on_task_id)
        if status is None or status == TaskWorkflowStatus.done:
            continue
        depended = tasks_service.get_task(db, dep.depends_on_task_id)
        if depended is not None:
            unfinished.append(
                BlockingTask(
                    task_id=depended.id,
                    title=depended.title,
                    workflow_status=status,
                )
            )
    return unfinished
