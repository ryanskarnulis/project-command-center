from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskWorkflowStatus
from app.services.common import soft_delete
from app.services.tasks import (
    RecurrenceError,
    get_rollup,
    get_task,
    list_subtasks,
    log_task_event,
    soft_delete_task,
)


def _next_due_date(due_date: date, interval: Mapping[str, Any]) -> date:
    """The next occurrence's due date: ``due_date`` advanced by one interval.

    ``day``/``week`` are exact ``timedelta`` offsets. ``month`` uses manual month
    arithmetic with day-clamping (Jan 31 + 1 month -> Feb 28) since calendar
    months vary in length and ``python-dateutil`` is deliberately not a dependency.
    """
    unit = interval["unit"]
    every = int(interval["every"])
    if unit == "day":
        return due_date + timedelta(days=every)
    if unit == "week":
        return due_date + timedelta(weeks=every)
    if unit == "month":
        month_index = (due_date.month - 1) + every
        year = due_date.year + month_index // 12
        month = month_index % 12 + 1
        last_day = monthrange(year, month)[1]
        return date(year, month, min(due_date.day, last_day))
    raise ValueError(f"Unknown recurrence unit: {unit!r}")


def next_occurrence_date(task: Task) -> date | None:
    """When this recurring task repeats next, or ``None`` if it never will.

    Derived for the read payload so the UI can show "next <date>" beside the
    repeat badge without re-implementing the interval math in TypeScript. Only an
    open recurring task with a due date has a next occurrence — a done task has
    already spawned its successor as a separate row, and a task without a due date
    or interval isn't scheduled.
    """
    if task.repeat_interval is None or task.due_date is None:
        return None
    if task.workflow_status == TaskWorkflowStatus.done:
        return None
    return _next_due_date(task.due_date, task.repeat_interval)


def _clone_subtask_tree(
    db: Session, source: Task, new_parent_id: int, due_date: date | None
) -> None:
    """Recursively clone ``source``'s active subtree under ``new_parent_id``.

    Each clone resets to open and carries no recurrence (only the series head is a
    series member); title/description/priority/estimate are copied. Every clone
    inherits ``due_date`` (the new occurrence's date) so the reset checklist is due
    with its occurrence rather than carrying the previous cadence's stale dates.
    Grandchildren recurse.
    """
    for child in list_subtasks(db, source.id):
        clone = Task(
            project_id=child.project_id,
            title=child.title,
            description=child.description,
            priority=child.priority,
            estimated_minutes=child.estimated_minutes,
            repeat_interval=None,
            recurrence_id=None,
            due_date=due_date,
            workflow_status=TaskWorkflowStatus.open,
            parent_task_id=new_parent_id,
        )
        db.add(clone)
        db.flush()
        db.refresh(clone)
        log_task_event(db, clone, "created")
        _clone_subtask_tree(db, child, clone.id, due_date)


def _find_occurrence_on(db: Session, recurrence_id: str, due_date: date) -> Task | None:
    """The series' existing occurrence due on ``due_date``, or ``None``.

    Deliberately does not filter ``deleted_at``: a skipped occurrence is
    soft-deleted but still *happened* as a scheduling decision, and respawning its
    date would re-add work the user explicitly said they didn't do. Same reasoning
    as ``get_series``. An active row wins over a soft-deleted one on the same date
    so callers get the live occurrence when both exist.
    """
    return (
        db.execute(
            select(Task)
            .where(Task.recurrence_id == recurrence_id, Task.due_date == due_date)
            .order_by(Task.deleted_at.is_not(None), Task.id.asc())
        )
        .scalars()
        .first()
    )


def create_next_occurrence(db: Session, task: Task) -> Task:
    """Clone a completed recurring task as its next open occurrence.

    Copies title/description/priority/estimate/project and the shared
    ``recurrence_id``, advances the due date by one interval, and files the clone
    as an open, top-level task (occurrences are never subtasks — see the sprint's
    out-of-scope note). The caller guarantees ``repeat_interval`` and ``due_date``
    are set.

    Idempotent on ``(recurrence_id, next due date)``: if that occurrence already
    exists it is returned as-is rather than inserted again. Completion is not a
    once-only event — reopening a done occurrence and re-completing it makes the
    open->done transition a second time, and a checklist's roll-up re-derives to
    done whenever its last child is reopened and re-completed. Both spawn paths
    land here, so the guard lives here rather than in each caller. Reopening
    deliberately leaves an already-spawned successor alone (no hard deletes, and
    the successor may have its own progress); this guard is what makes the
    re-completion a no-op instead of a duplicate.

    If the recurring task is a checklist parent, its whole active subtree is
    cloned fresh under the new occurrence so a multi-step routine ("weekly release
    checklist") resets for the next cadence. A recurring leaf clones a single row.
    """
    assert task.repeat_interval is not None
    assert task.due_date is not None
    next_due = _next_due_date(task.due_date, task.repeat_interval)
    if task.recurrence_id is not None:
        existing = _find_occurrence_on(db, task.recurrence_id, next_due)
        if existing is not None:
            return existing
    occurrence = Task(
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        priority=task.priority,
        estimated_minutes=task.estimated_minutes,
        repeat_interval=task.repeat_interval,
        recurrence_id=task.recurrence_id,
        due_date=next_due,
        workflow_status=TaskWorkflowStatus.open,
        parent_task_id=None,
    )
    db.add(occurrence)
    db.flush()
    db.refresh(occurrence)
    log_task_event(db, occurrence, "created")
    _clone_subtask_tree(db, task, occurrence.id, occurrence.due_date)
    return occurrence


def maybe_spawn_recurring_checklist(db: Session, completed_child: Task) -> None:
    """Advance the series when completing a child finishes a recurring checklist.

    A checklist parent's status is derived (read-only), so it never makes the
    stored open->done transition that spawns the next occurrence. Instead, when a
    child completes we walk up to the nearest recurring ancestor and, if its whole
    subtree now rolls up to done, spawn that ancestor's next occurrence. The last
    child to complete is the only one that makes the subtree done, so this fires
    once. Only the nearest recurring ancestor spawns — a series-within-a-series
    can't double-fire.
    """
    visited: set[int] = set()
    current_id = completed_child.parent_task_id
    while current_id is not None and current_id not in visited:
        visited.add(current_id)
        ancestor = get_task(db, current_id)
        if ancestor is None:
            return
        if ancestor.repeat_interval is not None and ancestor.due_date is not None:
            if get_rollup(db, ancestor).workflow_status == TaskWorkflowStatus.done:
                create_next_occurrence(db, ancestor)
            return
        current_id = ancestor.parent_task_id


def skip_occurrence(db: Session, task: Task) -> Task:
    """Skip the current recurring occurrence: soft-delete it and roll forward.

    "Skip this one" means this occurrence never happened — it's removed from active
    lists (recoverable in trash), and the series continues with the next occurrence.
    Unlike completion, the skipped row is not recorded as ``done`` (that would
    pollute completed-task history with work the user explicitly didn't do).

    Rejects a non-recurring task or one without a due date with a 422: there is
    nothing to roll forward to.
    """
    if task.repeat_interval is None or task.due_date is None:
        raise RecurrenceError(
            "Only a recurring task with a due date can be skipped"
        )
    next_occurrence = create_next_occurrence(db, task)
    # Cascade the skip across the occurrence's subtree. The next occurrence is
    # cloned first (above), so the children can now go to trash with the parent:
    # otherwise a checklist occurrence's subtasks stay active pointing at a
    # soft-deleted parent, and the frontend's buildTaskTree promotes them to
    # root-level orphans (one leaked copy per skip). Children cascade-delete first
    # (each logging "deleted"); the occurrence row itself is logged as "skipped".
    for child in list_subtasks(db, task.id):
        soft_delete_task(db, child)
    soft_delete(task)
    # Persist the *intent*, not just the deletion: an ordinary delete also sets
    # deleted_at, and restore_task must not treat that as an un-skip (it would
    # drag the live occurrence backward and purge the restored row).
    task.skipped_at = task.deleted_at
    db.flush()
    log_task_event(db, task, "skipped")
    return next_occurrence


def get_series(db: Session, recurrence_id: str) -> list[Task]:
    """The series' active and skipped occurrences, oldest due date first.

    Deliberately not the ``active()`` helper: a skipped occurrence is soft-deleted,
    but it's a scheduling decision the user made and the timeline must show it for
    the chain to be truthful. A *normally trashed* occurrence is not — it's in the
    trash, so it leaves the timeline and reappears if restored.

    That filter is load-bearing for the client: it's what lets the timeline read a
    ``deleted_at`` row as "Skipped" without also needing ``skipped_at`` on the wire.
    Ordered by ``due_date`` (then ``id`` as a stable tiebreak for rows sharing a date).
    """
    return list(
        db.execute(
            select(Task)
            .where(
                Task.recurrence_id == recurrence_id,
                or_(Task.deleted_at.is_(None), Task.skipped_at.is_not(None)),
            )
            .order_by(Task.due_date.asc(), Task.id.asc())
        )
        .scalars()
        .all()
    )


def stop_recurrence(db: Session, task: Task) -> Task:
    """Stop a series from spawning further occurrences.

    Clears ``repeat_interval`` (so completing the task no longer creates the next
    occurrence) while leaving ``recurrence_id`` intact, matching the inline-clear
    rule in ``update_task`` so the existing chain stays readable. Rejects a
    non-recurring task with a 422 — there is nothing to stop.
    """
    if task.repeat_interval is None:
        raise RecurrenceError("Task is not recurring")
    task.repeat_interval = None
    db.flush()
    db.refresh(task)
    log_task_event(db, task, "updated")
    return task


def reschedule_occurrence(db: Session, occurrence: Task, new_due: date) -> None:
    """Set this occurrence and its entire active subtree to ``new_due``.

    Occurrence subtasks all share the occurrence's due date (see
    ``_clone_subtask_tree``), so an un-skip is a flat date reset down the tree.
    """
    occurrence.due_date = new_due
    for child in list_subtasks(db, occurrence.id):  # active children only
        reschedule_occurrence(db, child, new_due)
