from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FocusPlanEntry, Task, TaskPriority, TaskWorkflowStatus
from app.schemas.focus import (
    BlockedTask,
    BlockingTask,
    CommitmentBlock,
    CommitmentRepeatInterval,
    DueSignal,
    OverflowTask,
    ScheduledBlock,
    SkippedCommitmentBlock,
    FocusPlan,
)
from app.services import focus_plan as focus_plan_service
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
    """The effective status — rolled up over children, dependency cap included.

    The roll-up already applies the cap at every depth (``tasks._resolve_rollup``),
    so a parent over a subtask held open by its own blocker ranks as under way
    rather than done. Blocked tasks themselves are split into their own
    ``blocked`` list before any status is displayed or scheduled (see
    ``get_focus_plan`` and ``_schedulable_subtasks``).
    """
    return rollups[task.id].workflow_status


def _rank_key(
    task: Task, target_date: date, rollups: Rollups
) -> tuple[int, int, int, int, int]:
    """Deterministic sort key (ascending). The v1 focus-ranking rules.

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


def _free_gaps(
    window_start: int, window_end: int, busy: Sequence[tuple[int, int]]
) -> list[list[int]]:
    """The window minus the fixed blocks, as ``[start, end)`` gaps in time order.

    Overlapping fixed blocks are merged for this purpose (two meetings at once
    still only take the time once). With no fixed blocks this is the whole
    window — one gap, which is what makes the packer degrade to the old
    back-to-back pack.
    """
    gaps: list[list[int]] = []
    cursor = window_start
    for start, end in sorted(busy):
        if start > cursor:
            gaps.append([cursor, min(start, window_end)])
        cursor = max(cursor, end)
        if cursor >= window_end:
            break
    if cursor < window_end:
        gaps.append([cursor, window_end])
    return [gap for gap in gaps if gap[1] > gap[0]]


def _place(minutes: int, gaps: list[list[int]]) -> int | None:
    """First fit: take ``minutes`` from the front of the first gap that holds them.

    Returns the block's start minute and shrinks that gap, or ``None`` when no gap
    is big enough. First fit is what lets a small lower-ranked task use the time
    left before a meeting after a bigger one skipped past it.
    """
    for gap in gaps:
        if gap[1] - gap[0] >= minutes:
            start = gap[0]
            gap[0] += minutes
            return start
    return None


@dataclass(frozen=True)
class _Pin:
    """A live task pin the packer honours, with its task already resolved."""

    task: Task
    entry: FocusPlanEntry


def _pack(
    db: Session,
    ranked: Sequence[Task],
    start_minutes: int,
    available_minutes: int,
    target_date: date,
    rollups: Rollups,
    blocked_ids: set[int],
    *,
    floating: Sequence[_Pin] = (),
    timed: Sequence[_Pin] = (),
    busy: Sequence[tuple[int, int]] = (),
) -> tuple[list[ScheduledBlock], list[OverflowTask], int]:
    """Place pinned and ranked tasks into the free gaps around the fixed blocks.

    Timed pins sit at their own time and never move; ``busy`` carries the fixed
    intervals that are not tasks (commitments) so both are packed around. Floating
    pins come next, in their stored order, and never overflow: one that fits no
    gap is placed after the end of the day — the user asked for it. Then the
    ranked list, first fit in rank order: a task too large for every gap goes to
    overflow and scanning continues, so smaller lower-ranked tasks still fill the
    day. Before overflowing a too-large parent, its open subtasks are tried in its
    rank slot — each one that fits is scheduled as its own block (labelled with
    the parent), so a big task still makes partial progress.

    With no pins and no commitments there is one gap and this is the original
    back-to-back greedy pack. Scheduled blocks are returned in time order and
    overflow in ranked order. ``used`` counts every block's minutes inside the
    day from ``start_minutes`` on, fixed ones and late pins included, so it can
    exceed ``available_minutes``.
    """
    blocks: list[ScheduledBlock] = []
    overflow: list[OverflowTask] = []
    used = 0
    window_end = start_minutes + available_minutes

    def _schedule(
        task: Task,
        minutes: int,
        assumed: bool,
        parent: Task | None,
        block_start: int,
        entry: FocusPlanEntry | None = None,
        fixed: bool = False,
        pin_order: int | None = None,
    ) -> None:
        nonlocal used
        used += max(0, block_start + minutes - max(block_start, start_minutes))
        reason = _reason(task, target_date, rollups)
        if parent is not None:
            reason = f"part of {parent.title} · {reason}"
        block_start_time, start_day_offset = _format_time(block_start)
        block_end_time, end_day_offset = _format_time(block_start + minutes)
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
                entry_id=entry.id if entry is not None else None,
                pinned=entry is not None,
                fixed=fixed,
                pin_order=pin_order,
            )
        )

    fixed_intervals = list(busy)
    for pin in timed:
        assert pin.entry.start_minute is not None
        minutes, assumed = _effective_estimate(pin.task, rollups)
        _schedule(
            pin.task, minutes, assumed, None, pin.entry.start_minute, pin.entry, fixed=True
        )
        fixed_intervals.append(
            (pin.entry.start_minute, pin.entry.start_minute + minutes)
        )
    gaps = _free_gaps(start_minutes, window_end, fixed_intervals)
    # Where a floating pin that fits no gap goes: after the window and every
    # fixed block, one after another.
    late_cursor = max([window_end, *(end for _start, end in fixed_intervals)])

    for order, pin in enumerate(floating):
        minutes, assumed = _effective_estimate(pin.task, rollups)
        block_start = _place(minutes, gaps)
        if block_start is None:
            block_start = late_cursor
            late_cursor += minutes
        _schedule(
            pin.task, minutes, assumed, None, block_start, pin.entry, pin_order=order
        )

    for task in ranked:
        minutes, assumed = _effective_estimate(task, rollups)
        block_start = _place(minutes, gaps)
        if block_start is not None:
            _schedule(task, minutes, assumed, None, block_start)
            continue
        # Too large for every gap: try the parent's own subtasks in this rank
        # slot before overflowing it, so part of the work still lands today.
        scheduled_subtasks = 0
        for sub in _schedulable_subtasks(db, task, target_date, rollups, blocked_ids):
            sub_minutes, sub_assumed = _effective_estimate(sub, rollups)
            sub_start = _place(sub_minutes, gaps)
            if sub_start is not None:
                _schedule(sub, sub_minutes, sub_assumed, task, sub_start)
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
    blocks.sort(key=lambda block: _absolute_minute(block.start_time, block.start_day_offset))
    return blocks, overflow, used


def _absolute_minute(clock: str, day_offset: int) -> int:
    return day_offset * 24 * 60 + _parse_time(clock)


def _commitment_block(entry: FocusPlanEntry) -> CommitmentBlock:
    assert entry.start_minute is not None and entry.duration_minutes is not None
    assert entry.title is not None
    start_time, start_day_offset = _format_time(entry.start_minute)
    end_time, end_day_offset = _format_time(entry.start_minute + entry.duration_minutes)
    return CommitmentBlock(
        entry_id=entry.id,
        title=entry.title,
        start_time=start_time,
        end_time=end_time,
        start_day_offset=start_day_offset,
        end_day_offset=end_day_offset,
        duration_minutes=entry.duration_minutes,
        repeat_interval=CommitmentRepeatInterval.model_validate(entry.repeat_interval)
        if entry.repeat_interval is not None
        else None,
    )


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
    # Non-deleted task ids (done ones included) so an orphan — a live child whose
    # parent is soft-deleted — is promoted to top-level here instead of vanishing
    # from the plan, while a subtask of a merely-completed parent stays nested.
    active_ids = set(
        db.execute(select(Task.id).where(Task.deleted_at.is_(None))).scalars()
    )
    source = [
        task
        for task in tasks_service.list_tasks(db, exclude_done=True)
        # Subtasks are scheduled as part of their parent's work, never as their
        # own day-plan rows (except as stand-ins when the parent doesn't fit —
        # see _pack). Deferred tasks are snoozed out of the plan entirely.
        if tasks_service.is_effective_top_level(task, active_ids)
        and not _is_deferred(task, target_date)
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
    blocking_by_task = _unfinished_dependencies_for(
        db, [task.id for task in source if task.id in blocked_ids]
    )
    for task in source:
        if task.id in blocked_ids:
            blocked.append(
                BlockedTask(
                    task_id=task.id,
                    title=task.title,
                    project_id=task.project_id,
                    priority=task.priority,
                    due_date=task.due_date,
                    blocking_tasks=blocking_by_task.get(task.id, []),
                )
            )
        else:
            schedulable.append(task)

    window_start = _parse_time(start_time)
    # The stored overlay (#319). A pin only counts while its task is still
    # schedulable here — done, trashed, deferred past the day, blocked or nested
    # pins are ignored, never deleted, so deferring a pinned task still makes it
    # leave the day. A commitment that ended before the window is past; a timed
    # pin that did, with its task still open, floats instead — it is still
    # pinned to today.
    schedulable_by_id = {task.id: task for task in schedulable}
    floating: list[_Pin] = []
    timed: list[_Pin] = []
    commitments: list[CommitmentBlock] = []
    busy: list[tuple[int, int]] = []
    # Commitments: the day's one-offs plus the occurrences of repeating series
    # (#376), worked out for this day on read.
    for entry in focus_plan_service.day_commitments(db, target_date):
        assert entry.start_minute is not None and entry.duration_minutes is not None
        end = entry.start_minute + entry.duration_minutes
        if end <= window_start:
            continue
        commitments.append(_commitment_block(entry))
        busy.append((entry.start_minute, end))
    # Skipped occurrences (#429) hold no time; they are listed so a skip can be
    # taken back later, with the same "already over" cut as live commitments.
    skipped_commitments: list[SkippedCommitmentBlock] = []
    for series, skip in focus_plan_service.day_skipped_commitments(db, target_date):
        assert series.start_minute is not None and series.duration_minutes is not None
        if series.start_minute + series.duration_minutes <= window_start:
            continue
        skipped_commitments.append(
            SkippedCommitmentBlock(
                **_commitment_block(series).model_dump(), skip_entry_id=skip.id
            )
        )
    for entry in focus_plan_service.list_entries(db, target_date):
        if entry.task_id is None:
            continue
        pinned_task = schedulable_by_id.get(entry.task_id)
        if pinned_task is None:
            continue
        pin = _Pin(task=pinned_task, entry=entry)
        minutes, _assumed = _effective_estimate(pinned_task, rollups)
        if entry.start_minute is not None and entry.start_minute + minutes > window_start:
            timed.append(pin)
        else:
            floating.append(pin)
    pinned_ids = {pin.task.id for pin in (*floating, *timed)}

    ranked = sorted(
        (task for task in schedulable if task.id not in pinned_ids),
        key=lambda task: _rank_key(task, target_date, rollups),
    )
    blocks, overflow, used = _pack(
        db,
        ranked,
        window_start,
        available_minutes,
        target_date,
        rollups,
        blocked_ids,
        floating=floating,
        timed=timed,
        busy=busy,
    )
    used += sum(
        block.duration_minutes
        - max(0, window_start - _absolute_minute(block.start_time, block.start_day_offset))
        for block in commitments
    )
    commitments.sort(key=lambda block: _absolute_minute(block.start_time, block.start_day_offset))

    return FocusPlan(
        date=target_date,
        start_time=start_time,
        available_minutes=available_minutes,
        used_minutes=used,
        scheduled=blocks,
        commitments=commitments,
        skipped_commitments=skipped_commitments,
        overflow=overflow,
        blocked=blocked,
    )


def _unfinished_dependencies_for(
    db: Session, task_ids: Sequence[int]
) -> dict[int, list[BlockingTask]]:
    """Per task, its active dependencies whose target is not yet done.

    Returns each blocker's title + workflow status (not just the id) so the UI can
    render a self-explanatory blocked row. The status reported is the *effective*
    one (``deps_service.effective_statuses``), matching both the decision that the
    blocker is unfinished and the status the task detail page shows.

    Batched over the whole blocked set — one edge query, one status resolution,
    one blocker fetch. Asking per task cost a ``list_dependencies`` plus a full
    ``effective_statuses`` plus a ``get_task`` per unfinished blocker, for every
    blocked row in the plan.
    """
    if not task_ids:
        return {}
    edges_by_task = deps_service.list_dependencies_for(db, task_ids)
    blocker_ids = {
        edge.depends_on_task_id
        for edges in edges_by_task.values()
        for edge in edges
    }
    statuses = deps_service.effective_statuses(db, blocker_ids)
    blockers = tasks_service.get_tasks(db, blocker_ids)

    result: dict[int, list[BlockingTask]] = {}
    for task_id, edges in edges_by_task.items():
        unfinished: list[BlockingTask] = []
        for edge in edges:
            status = statuses.get(edge.depends_on_task_id)
            if status is None or status == TaskWorkflowStatus.done:
                continue
            depended = blockers.get(edge.depends_on_task_id)
            if depended is not None:
                unfinished.append(
                    BlockingTask(
                        task_id=depended.id,
                        title=depended.title,
                        workflow_status=status,
                    )
                )
        result[task_id] = unfinished
    return result
