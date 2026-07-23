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
    return _insert_occurrence(db, task, next_due)


def _insert_occurrence(db: Session, task: Task, due_date: date) -> Task:
    """Clone ``task`` as a fresh open occurrence due on ``due_date``.

    The raw insert with no idempotency check — callers decide whether a row may
    already exist on the date. Copies the series identity and content, files the
    clone as an open top-level task, and clones the active subtree so a checklist
    routine resets for the new cadence.
    """
    occurrence = Task(
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        priority=task.priority,
        estimated_minutes=task.estimated_minutes,
        repeat_interval=task.repeat_interval,
        recurrence_id=task.recurrence_id,
        due_date=due_date,
        workflow_status=TaskWorkflowStatus.open,
        parent_task_id=None,
    )
    db.add(occurrence)
    db.flush()
    db.refresh(occurrence)
    log_task_event(db, occurrence, "created")
    _clone_subtask_tree(db, task, occurrence.id, occurrence.due_date)
    return occurrence


def _next_live_occurrence(db: Session, task: Task) -> Task:
    """The live occurrence a skip rolls forward to, guaranteeing one on the wire.

    ``create_next_occurrence``'s idempotency guard returns whatever
    ``_find_occurrence_on`` finds on the next date — including a *soft-deleted*
    row. That's right for re-completion (don't respawn), but wrong for a skip: the
    caller expects the next occurrence to be a live, actionable row. So skip needs
    its own roll-forward that always lands a live occurrence on the correct date:

    - Empty date, or a date whose only row is *normally trashed* → insert a fresh
      live occurrence there (the trashed row stays in the trash; a later restore of
      it may leave two live rows on the date, which is accepted).
    - Date already holds a *live* row → return it (no duplicate).
    - Date holds a *skipped* row (``skipped_at`` set) → honor that earlier skip and
      advance to the following date. The live successor spawned when that date was
      skipped normally sits just beyond it, so this returns that existing live row.

    ``_find_occurrence_on`` is left untouched, so the completion/idempotency path
    keeps its no-duplicate, no-data-loss behavior.
    """
    assert task.repeat_interval is not None
    assert task.due_date is not None
    current_due = task.due_date
    while True:
        next_due = _next_due_date(current_due, task.repeat_interval)
        existing = (
            _find_occurrence_on(db, task.recurrence_id, next_due)
            if task.recurrence_id is not None
            else None
        )
        if existing is None:
            return _insert_occurrence(db, task, next_due)
        if existing.deleted_at is None:
            return existing
        if existing.skipped_at is not None:
            # Explicitly skipped date: don't revive it — advance past it.
            current_due = next_due
            continue
        # Normally-trashed row on this date: land a fresh live occurrence here.
        return _insert_occurrence(db, task, next_due)


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
            # Local import: task_dependencies builds on tasks, which this module
            # already depends on — a top-level import would cycle.
            from app.services import task_dependencies as deps_service

            if (
                get_rollup(db, ancestor).workflow_status == TaskWorkflowStatus.done
                and not deps_service.is_blocked(db, ancestor.id)
            ):
                create_next_occurrence(db, ancestor)
            # A blocked recurring parent doesn't spawn here; completing its
            # blocker later drives the deferred spawn via
            # spawn_unblocked_recurring_dependents.
            return
        current_id = ancestor.parent_task_id


def spawn_unblocked_recurring_dependents(db: Session, completed: Task) -> None:
    """Spawn recurrences that a just-completed blocker has now unblocked.

    A recurring checklist parent whose children were all completed while it was
    still blocked by an unfinished dependency does *not* spawn its next occurrence
    at child-completion time (see ``maybe_spawn_recurring_checklist``). Completing
    that blocker is what should roll the series forward, so on every completion we
    walk the dependents of ``completed`` and, for each one that is now effectively
    done and no longer blocked, spawn it.

    Propagates transitively: finishing a task can make a non-recurring dependent
    effectively done too, which in turn unblocks *its* recurring dependents. The
    ``seen`` set bounds the walk; ``create_next_occurrence`` is idempotent on
    ``(recurrence_id, next due date)`` so a dependent reached by more than one path
    spawns at most once.
    """
    from app.services import task_dependencies as deps_service

    queue = [completed.id]
    seen: set[int] = set()
    while queue:
        current_id = queue.pop()
        for edge in deps_service.list_dependents(db, current_id):
            dependent_id = edge.task_id
            if dependent_id in seen:
                continue
            dependent = get_task(db, dependent_id)
            if dependent is None:
                continue
            if deps_service.is_blocked(db, dependent_id):
                continue
            if get_rollup(db, dependent).workflow_status != TaskWorkflowStatus.done:
                continue
            # Now effectively done and unblocked. Spawn if it's a recurring series
            # head, and propagate: it just became a satisfied blocker for its own
            # downstream.
            seen.add(dependent_id)
            if (
                dependent.repeat_interval is not None
                and dependent.due_date is not None
            ):
                create_next_occurrence(db, dependent)
            queue.append(dependent_id)


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
    next_occurrence = _next_live_occurrence(db, task)
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


def _series_rows(db: Session, recurrence_id: str) -> list[Task]:
    """Every row sharing this ``recurrence_id``, trashed and skipped ones included."""
    return list(
        db.execute(select(Task).where(Task.recurrence_id == recurrence_id))
        .scalars()
        .all()
    )


def stop_recurrence(db: Session, task: Task) -> Task:
    """Stop a series from spawning further occurrences, from any occurrence in it.

    A series is not one row: ``create_next_occurrence`` copies ``repeat_interval``
    onto every clone, and completing an occurrence never clears it. So "stop" has
    to clear the interval across the whole ``recurrence_id``, not just the row the
    user happened to be looking at — the timeline links past occurrences, and
    stopping from one of those used to clear a dead row while the live successor
    kept spawning.

    Deliberately not scoped by ``deleted_at`` the way ``get_series`` is: a trashed
    occurrence still holds its ``repeat_interval``, and restoring it later would
    resume the series the user just stopped.

    ``recurrence_id`` is left intact so the existing chain stays readable, matching
    the inline-clear rule in ``update_task``. Rejects a series with nothing left to
    stop with a 422.
    """
    rows = (
        _series_rows(db, task.recurrence_id)
        if task.recurrence_id is not None
        # Defensive: tasks.update_task mints a recurrence_id whenever
        # repeat_interval is set, so a recurring row without one shouldn't exist.
        else [task]
    )
    recurring = [row for row in rows if row.repeat_interval is not None]
    if not recurring:
        raise RecurrenceError("Task is not recurring")
    for row in recurring:
        row.repeat_interval = None
    db.flush()
    for row in recurring:
        log_task_event(db, row, "updated")
    db.refresh(task)
    return task


def reschedule_occurrence(db: Session, occurrence: Task, new_due: date) -> None:
    """Set this occurrence and its entire active subtree to ``new_due``.

    Occurrence subtasks all share the occurrence's due date (see
    ``_clone_subtask_tree``), so an un-skip is a flat date reset down the tree.
    """
    occurrence.due_date = new_due
    for child in list_subtasks(db, occurrence.id):  # active children only
        reschedule_occurrence(db, child, new_due)
