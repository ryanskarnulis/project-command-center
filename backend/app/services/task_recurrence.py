from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency, TaskWorkflowStatus
from app.services.common import soft_delete
from app.services.integrity import violates_unique_columns
from app.services.tasks import (
    OccurrenceConflictError,
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


def next_occurrence_date(
    task: Task, effective_status: TaskWorkflowStatus
) -> date | None:
    """When this recurring task repeats next, or ``None`` if it never will.

    Derived for the read payload so the UI can show "next <date>" beside the
    repeat badge without re-implementing the interval math in TypeScript. Only a
    recurring task with a due date that is *not yet effectively done* has a next
    occurrence: once it is done its successor already exists as its own row, so
    advertising the date again would point at a task the user can already see.

    ``effective_status`` — not the stored one — because a checklist parent's
    status is derived: it stays stored-``open`` forever while its rolled-up state
    goes ``done``, and reading the stored value made such a parent advertise a
    date whose occurrence had already been spawned. Callers resolve it with
    ``capped_status`` (they already compute the rollups for the same payload).
    """
    if task.repeat_interval is None or task.due_date is None:
        return None
    if effective_status is TaskWorkflowStatus.done:
        return None
    return _next_due_date(task.due_date, task.repeat_interval)


def _clone_subtask_tree(
    db: Session,
    source: Task,
    new_parent_id: int,
    due_date: date | None,
    clone_ids: dict[int, int],
) -> None:
    """Recursively clone ``source``'s active subtree under ``new_parent_id``.

    Each clone resets to open and carries no recurrence (only the series head is a
    series member); title/description/priority/estimate are copied. Every clone
    inherits ``due_date`` (the new occurrence's date) so the reset checklist is due
    with its occurrence rather than carrying the previous cadence's stale dates.
    Grandchildren recurse.

    ``clone_ids`` accumulates ``source subtask id -> clone id`` for the whole
    subtree; ``_clone_dependency_edges`` needs the finished map to remap edges, so
    the two passes are deliberately separate (an edge may point at a task in a
    sibling branch that has not been cloned yet when this one is walked).
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
        clone_ids[child.id] = clone.id
        log_task_event(db, clone, "created")
        _clone_subtask_tree(db, child, clone.id, due_date, clone_ids)


def _clone_dependency_edges(db: Session, clone_ids: Mapping[int, int]) -> None:
    """Recreate the cloned subtree's *internal* dependency edges on the clones.

    A checklist's ordering ("Deploy waits on Build") is part of the routine, not
    incidental metadata: without this the successor occurrence's steps are all
    unblocked and completable out of order.

    **Only edges with both endpoints inside the cloned subtree are recreated.** An
    edge to a task *outside* the subtree is deliberately dropped rather than
    pointed at the original task, because the outside endpoint belongs to *this*
    cadence: a one-off blocker that will already be done (leaving the clone
    permanently satisfied and the edge pure noise), or worse, a task in the
    completed occurrence's own tree, which would chain every future occurrence to
    the previous one's rows. Re-establishing such a cross-cadence link is a
    judgement the user should make explicitly on the new occurrence; guessing it
    here would silently manufacture blockers nobody asked for.

    Goes through ``add_dependency`` rather than inserting rows, so clone edges get
    the same validation and the same ``dependency_added`` activity event a manual
    add produces — the clone is a real workflow change and the feed should say so.
    Edges are replayed in source-edge id order for a deterministic audit trail.
    """
    # Local import: task_dependencies builds on this module (see ``reconcile``), so
    # a top-level import would cycle.
    from app.services import task_dependencies as deps_service

    if not clone_ids:
        return
    source_ids = set(clone_ids)
    edges = (
        db.execute(
            select(TaskDependency)
            .where(
                TaskDependency.deleted_at.is_(None),
                TaskDependency.task_id.in_(source_ids),
                TaskDependency.depends_on_task_id.in_(source_ids),
            )
            .order_by(TaskDependency.id)
        )
        .scalars()
        .all()
    )
    for edge in edges:
        deps_service.add_dependency(
            db, clone_ids[edge.task_id], clone_ids[edge.depends_on_task_id]
        )


def find_live_occurrence_on(
    db: Session, recurrence_id: str, due_date: date, *, exclude_id: int | None = None
) -> Task | None:
    """The series' *active* occurrence due on ``due_date``, or ``None``.

    The uniqueness key for a series: at most one live row per
    ``(recurrence_id, due_date)``, enforced in the database by the partial unique
    index ``uq_tasks_active_occurrence`` and guarded here so callers get a clean
    error instead of an IntegrityError. ``exclude_id`` lets a restore ask "is the
    slot taken by someone *else*".
    """
    stmt = select(Task).where(
        Task.recurrence_id == recurrence_id,
        Task.due_date == due_date,
        Task.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Task.id != exclude_id)
    return db.execute(stmt.order_by(Task.id.asc())).scalars().first()


def _find_skipped_occurrence_on(
    db: Session, recurrence_id: str, due_date: date
) -> Task | None:
    """The series' *skipped* occurrence due on ``due_date``, or ``None``.

    A skipped row is soft-deleted but still *happened* as a scheduling decision:
    respawning its date would re-add work the user explicitly said they didn't do,
    so it blocks the slot even though it isn't live. A *normally trashed* row is
    not the same statement — "delete this" is not "I decided not to do this" — and
    deliberately does not block: its date stays available for a replacement, and
    restoring it later is what raises ``OccurrenceConflictError``.
    """
    return (
        db.execute(
            select(Task)
            .where(
                Task.recurrence_id == recurrence_id,
                Task.due_date == due_date,
                Task.skipped_at.is_not(None),
            )
            .order_by(Task.id.asc())
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

    Idempotent on ``(recurrence_id, next due date)``: if a *live* occurrence is
    already on that date it is returned as-is rather than inserted again.
    Completion is not a once-only event — ``reconcile`` runs after every mutation
    that can move a task into effective completion, and re-completing or
    re-deriving a done roll-up lands here again. The guard lives here rather than
    in each caller. Reopening deliberately leaves an already-spawned successor
    alone (no hard deletes, and the successor may have its own progress); this
    guard is what makes the re-completion a no-op instead of a duplicate.

    A *skipped* row on the date is never revived (the user said that occurrence
    didn't happen) — the series rolls **forward past it** to the first date that
    holds neither a live nor a skipped row, and lands the successor there.
    Returning the skipped row instead used to stall the series: with two
    consecutive skips, restoring the earlier one rewinds the live occurrence onto
    it (see ``task_trash.restore_task``) and completion then stopped dead on the
    remaining skip, leaving the series with nothing live. Rolling forward stays
    idempotent — a later date already holding a live row is returned as-is. A
    *normally trashed* row does not block at all: that date is vacant and gets a
    fresh live occurrence.

    If the recurring task is a checklist parent, its whole active subtree is
    cloned fresh under the new occurrence so a multi-step routine ("weekly release
    checklist") resets for the next cadence. A recurring leaf clones a single row.
    """
    assert task.repeat_interval is not None
    assert task.due_date is not None
    if task.recurrence_id is None:
        next_due = _next_due_date(task.due_date, task.repeat_interval)
        return _insert_occurrence(db, task, next_due)

    current_due = task.due_date
    while True:
        next_due = _next_due_date(current_due, task.repeat_interval)
        live = find_live_occurrence_on(db, task.recurrence_id, next_due)
        if live is not None:
            return live
        if _find_skipped_occurrence_on(db, task.recurrence_id, next_due) is None:
            break
        # Explicitly skipped date: don't revive it — advance past it.
        current_due = next_due

    # The guard above is a read, and two writers can both pass it — completing the
    # same recurring task twice at once, or the web UI racing the agent. Writers
    # start IMMEDIATE (see get_db_write), so the loser's transaction begins after
    # the winner commits and the guard simply sees the winner's row; this branch is
    # the backstop for a caller that reached here on a read session, where the
    # unique index would otherwise surface as a 500. A SAVEPOINT keeps the failed
    # insert from poisoning the caller's transaction, which may already hold the
    # completion this occurrence follows from.
    savepoint = db.begin_nested()
    try:
        occurrence = _insert_occurrence(db, task, next_due)
    except IntegrityError as exc:
        # SQLite names the constrained columns, not the partial index, so the
        # `(recurrence_id, due_date)` tuple is how `uq_tasks_active_occurrence`
        # identifies itself. We just attempted exactly that insert, so a
        # uniqueness failure on those columns can only be this index.
        if not violates_unique_columns(exc, "tasks", ("recurrence_id", "due_date")):
            raise
        savepoint.rollback()
        winner = find_live_occurrence_on(db, task.recurrence_id, next_due)
        if winner is None:
            # Rolling back to a savepoint does not release the transaction's read
            # snapshot, so a DEFERRED caller cannot see the winner it just lost to.
            # Report the conflict rather than retrying into the same blind spot.
            raise OccurrenceConflictError(
                f"Another occurrence of this series was created for {next_due} "
                "concurrently; retry the request."
            ) from exc
        return winner
    savepoint.commit()
    return occurrence


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
    clone_ids: dict[int, int] = {}
    _clone_subtask_tree(db, task, occurrence.id, occurrence.due_date, clone_ids)
    _clone_dependency_edges(db, clone_ids)
    return occurrence


def reconcile(db: Session, task_ids: Iterable[int]) -> list[Task]:
    """Spawn the successors of every series that is now effectively complete.

    The single recurrence entry point. Recurrence used to hang off the stored
    ``open -> done`` transition, but "complete" in this app is *derived* —
    ``capped_status(roll-up, blocked)`` — and a task enters that state through
    doors that are not status writes: its last child completing, its last blocking
    dependency being removed or trashed, or a ``repeat_interval`` being attached to
    something already done. Each of those used to leave the series stalled. So
    every mutation that can change effective completion calls this with the ids it
    touched, and the answer is recomputed rather than inferred from the transition.

    Walks outward from each id: up the parent chain (a child's completion is its
    ancestors' completion) and, from any task that is now effectively done, out to
    its dependents (satisfying a blocker can complete the thing it blocked, and
    that can cascade). ``seen`` bounds the walk over cycles and diamonds; ancestor
    climbing stops at the nearest recurring ancestor so a series-within-a-series
    can't double-fire.

    Idempotent: spawning goes through ``create_next_occurrence``, which is a no-op
    when the next date is already taken. Returns the occurrences that exist for the
    reconciled series, newest work first for the caller that wants to report one.
    """
    # Local imports: both modules build on this one's dependencies — a top-level
    # import would cycle (same deliberate inversion as tasks.py's).
    from app.services import task_dependencies as deps_service
    from app.services import tasks as tasks_service

    spawned: list[Task] = []
    queue = list(task_ids)
    seen: set[int] = set()
    while queue:
        current_id = queue.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        task = get_task(db, current_id)
        if task is None:  # missing or soft-deleted: nothing to reconcile
            continue
        is_series_head = task.repeat_interval is not None and task.due_date is not None
        if task.parent_task_id is not None and not is_series_head:
            queue.append(task.parent_task_id)
        effective = tasks_service.capped_status(
            get_rollup(db, task).workflow_status,
            deps_service.is_blocked(db, current_id),
        )
        if effective is not TaskWorkflowStatus.done:
            continue
        if is_series_head:
            spawned.append(create_next_occurrence(db, task))
        # Now a satisfied blocker: whatever waited on it may have just completed.
        for edge in deps_service.list_dependents(db, current_id):
            queue.append(edge.task_id)
    return spawned


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
    # Skip and completion roll forward by the same rule — advance past explicitly
    # skipped dates, reuse a live row already sitting on the target date, otherwise
    # insert one — so both always hand back a live, actionable occurrence.
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
