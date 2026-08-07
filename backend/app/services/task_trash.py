from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency
from app.services import activity
from app.services import projects as projects_service
from app.services.common import active, deleted, hard_delete, restore
from app.services.task_recurrence import (
    find_live_occurrence_on,
    reconcile,
    reschedule_occurrence,
)
from app.services.tasks import OccurrenceConflictError, log_task_event


def list_deleted_tasks(db: Session, *, limit: int = 50) -> Sequence[Task]:
    """Soft-deleted tasks, most-recently-deleted first.

    Excludes tasks cascade-deleted with their project — those belong to the
    project's trash entry and are restored with it, not as standalone rows.
    """
    return (
        db.execute(
            deleted(Task)
            .where(Task.deleted_with_project_id.is_(None))
            .order_by(Task.deleted_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def count_standalone_deleted_tasks(db: Session) -> int:
    """Trashed tasks that are independently restorable (not cascade-deleted with a project)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.deleted_at.is_not(None),
                Task.deleted_with_project_id.is_(None),
            )
        )
        or 0
    )


def get_deleted_task(db: Session, task_id: int) -> Task | None:
    return db.execute(
        deleted(Task).where(Task.id == task_id)
    ).scalar_one_or_none()


def restore_task(db: Session, task: Task, *, defer_reconcile: bool = False) -> Task:
    """Restore one trashed task.

    ``defer_reconcile`` suppresses the recurrence reconciliation this restore
    would normally run, leaving it to the caller (see ``restore_task_subtree``).
    It only applies to the plain-restore path: the un-skip rewind below rewrites
    and destroys rows as one atomic correction and must settle immediately.

    Restoring a *skipped* occurrence is a subtree operation either way. With a
    live successor the rewind below brings the checklist back as that successor's
    (already cloned) subtree; without one, the plain restore takes the rows the
    skip cascaded away with it (issue #241). An ordinary trashed task is
    unchanged — root-only, with ``restore_task_subtree`` as the opt-in.
    """
    # Un-skip: if this occurrence's series still has a live occurrence, restoring
    # must NOT add a second one (that's the duplicate-series bug). Pull the live
    # occurrence's date (and its subtasks') back to the restored occurrence's date,
    # then hard-delete the restored row — the series resumes at the un-skipped date
    # with exactly one live occurrence.
    #
    # Gated on ``skipped_at``: this rewinds the series and destroys the restored
    # row, which is only what the user meant if they SKIPPED it. A normally-trashed
    # occurrence falls through to the plain restore below and comes back in place.
    if (
        task.skipped_at is not None
        and task.recurrence_id is not None
        and task.due_date is not None
    ):
        # The occurrence that replaced this one when it was skipped: the earliest
        # active sibling due on or after it. Filtering by date avoids retargeting an
        # earlier, already-completed occurrence (e.g. a done checklist parent).
        live = (
            db.execute(
                active(Task)
                .where(
                    Task.recurrence_id == task.recurrence_id,
                    Task.id != task.id,
                    Task.due_date >= task.due_date,
                )
                .order_by(Task.due_date.asc(), Task.id.asc())
            )
            .scalars()
            .first()
        )
        if live is not None:
            rewound = reschedule_occurrence(db, live, task.due_date)
            # Unscoped: this destroys the *original* skipped occurrence, which
            # the skip already replaced with fresh clones — including children
            # filed in other projects. The project-scoped walk would leave those
            # merely detached, stranding an obsolete duplicate in standalone
            # trash (BUG #212).
            purge_task(db, task, project_scoped=False)
            db.flush()
            reconcile(db, [live.id])
            db.flush()
            db.refresh(live)
            log_rewound_by_unskip(db, rewound, root=live)
            log_task_event(db, live, "restored")
            return live

    # Plain restore of a series occurrence: refuse if the date is already taken.
    # Skipping (or re-completing) after this row was trashed lands a live
    # replacement on its date, and bringing this one back would leave two active
    # occurrences of one series due the same day — the invariant the
    # uq_tasks_active_occurrence index enforces. Restoring is never allowed to be
    # the write that breaks it; the user trashes or skips the replacement first.
    if task.recurrence_id is not None and task.due_date is not None:
        conflict = find_live_occurrence_on(
            db, task.recurrence_id, task.due_date, exclude_id=task.id
        )
        if conflict is not None:
            raise OccurrenceConflictError(
                f"This series already has an active occurrence due "
                f"{task.due_date.isoformat()}. Trash or skip that one first, "
                f"then restore this."
            )

    # Read before the plain restore clears it: an un-skip with no live successor
    # has to bring the skipped occurrence's whole cascade back, not just its root.
    was_skipped = task.skipped_at is not None

    # Fallback (non-recurring, or a series with no live occurrence): plain restore.
    # A restored task may point at a since-deleted project; rehome it to General
    # so it stays reachable, mirroring the project-delete rehoming rule.
    if (
        task.project_id is not None
        and projects_service.get_project(db, task.project_id) is None
    ):
        task.project_id = projects_service.ensure_default_project_id(db)
    # An individually-restored task drops its cascade markers: it is back on its
    # own terms and must not be dragged around by a later parent/project restore.
    task.deleted_with_project_id = None
    task.deleted_with_task_id = None
    # And its skip marker: a skipped occurrence whose series has no live successor
    # restores in place through this path, and must not stay flagged as skipped
    # while it's active again.
    task.skipped_at = None
    restore(task)
    db.flush()
    # An un-skip that lands here is the inverse of ``skip_occurrence``, and that
    # skip trashed the occurrence *with* its checklist. The subtasks were never
    # trashed on their own terms, so leaving them behind would restore the
    # routine as an empty shell (issue #241). Rows already in the trash before
    # the skip carry no marker and stay there.
    restored_ids = [task.id]
    if was_skipped:
        restored_ids.extend(_restore_marked_descendants(db, task.id))
    # A restore brings a row back into every derived computation it was absent
    # from: a done occurrence returning to a series whose successor was purged is
    # immediately an effectively-done recurring task with nothing following it,
    # and a restored done child can complete an ancestor's roll-up. Seeding the
    # restored id covers both — reconcile climbs the parent chain and fans out to
    # dependents itself. Idempotent, so restoring an open task is a cheap no-op.
    #
    # A cascade restore defers this: reconciling mid-cascade judges the series
    # against a half-restored subtree (BUG #199).
    if not defer_reconcile:
        reconcile(db, restored_ids)
        db.flush()
    db.refresh(task)
    log_task_event(db, task, "restored")
    return task


def log_rewound_by_unskip(
    db: Session, rewound: Sequence[Task], *, root: Task
) -> None:
    """Audit the rows an un-skip's date rewind rewrote, excluding ``root``.

    ``reschedule_occurrence`` sets a whole checklist to the un-skipped date in one
    recursive pass. Nothing in that pass goes through ``update_task``, so without
    this every cloned subtask's user-visible due date changed with no trace and no
    actor — the attribution loss is total for an agent-driven un-skip (issue #243).

    ``updated`` is the action: from the row's own point of view this *is* an
    ordinary field edit, the same one ``update_task`` records when a due date is
    set by hand, and the same one ``stop_recurrence`` records for its bulk write.
    A new action string would be user-visible in the activity feed and would need
    frontend handling for no added meaning.

    ``root`` is excluded because it is the occurrence the caller reports as
    ``restored`` — the more meaningful event for the row the user acted on, and
    logging both would double-log one write. Only rows ``reschedule_occurrence``
    reported as *changed* are passed in, so a descendant already sitting on the
    target date gains no false event. ``log_task_event`` handles actor binding and
    unfiled (``project_id is None``) rows.
    """
    for node in rewound:
        if node.id == root.id:
            continue
        log_task_event(db, node, "updated")


def _marked_descendant_ids(db: Session, root_id: int) -> list[int]:
    """Ids of the trashed rows a single cascade delete of ``root_id`` removed.

    The marker is the whole point: it names exactly the rows that one delete took
    with it, so descendants the user had already trashed independently — no
    marker — are never swept back in.
    """
    return list(
        db.execute(
            select(Task.id)
            .where(
                Task.deleted_at.is_not(None),
                Task.deleted_with_task_id == root_id,
            )
            .order_by(Task.id.asc())
        )
        .scalars()
        .all()
    )


def _restore_marked_descendants(db: Session, root_id: int) -> list[int]:
    """Restore every trashed row stamped ``deleted_with_task_id == root_id``.

    The shared inverse of one cascade delete, used both by the opt-in
    ``restore_task_subtree`` and by the un-skip that has no successor to rewind.
    Each row goes back through ``restore_task`` so it gets its own ``restored``
    event, rehoming, and marker clearing; reconciliation is deferred to the
    caller, which judges the series once against the whole restored subtree
    (BUG #199). Returns the ids actually restored.
    """
    restored_ids: list[int] = []
    for descendant_id in _marked_descendant_ids(db, root_id):
        # Re-read: an un-skip that rewinds a live successor purges the original
        # tree, so a marked descendant may legitimately be gone by now.
        descendant = get_deleted_task(db, descendant_id)
        if descendant is None:
            continue
        restore_task(db, descendant, defer_reconcile=True)
        restored_ids.append(descendant_id)
    return restored_ids


def restore_task_subtree(db: Session, task: Task) -> tuple[Task, int]:
    """Restore ``task`` **and exactly the descendants trashed with it**.

    ``tasks.soft_delete_task`` cascades, so a single trash of a parent removes a
    whole subtree; restoring only the root leaves the checklist in the trash and
    the parent silently a leaf (BUG #192). This is the true inverse of that one
    delete: it brings back the rows stamped ``deleted_with_task_id == task.id``
    by that cascade and nothing else — descendants the user had already trashed
    independently beforehand carry no marker and stay in the trash, where they
    belong.

    Unlike the purge traversal (``_deleted_subtree_depth_first``) this is *not*
    project-scoped. That walk is scoped because it destroys rows and must not
    reach into another project's trash; here the marker already names the exact
    rows this delete removed, whatever project they live in, and restoring them
    is reversible anyway.

    Returns the restored root and the number of descendants restored with it.
    Each row goes through ``restore_task``, so every one gets its own
    ``restored`` activity event and rehoming.

    Recurrence, however, is reconciled **once, after the whole subtree is back**
    (BUG #199). Reconciling per-row meant the root — restored first, while its
    open children were still deleted and therefore invisible to the roll-up —
    looked effectively done and advanced its series; the checklist then came
    back open behind an already-spawned successor. Deferring to the final state
    keeps the invariant that a successor follows *effective* completion.
    """
    # Captured up front: an un-skip rewind hard-deletes ``task`` and hands back
    # the live successor instead, so neither the row nor its id survives the
    # restore below — and the marker set belongs to the row the user picked.
    task_id = task.id
    marked_ids = _marked_descendant_ids(db, task_id)
    root = restore_task(db, task, defer_reconcile=True)
    # Restoring the root may already have brought the marked rows back — an
    # un-skip with no live successor restores its cascade as one unit (issue
    # #241) — so this pass picks up whatever is still in the trash, and the count
    # is taken from what ended up active either way.
    _restore_marked_descendants(db, task_id)
    restored_ids = [
        descendant_id
        for descendant_id in marked_ids
        if db.execute(
            active(Task).where(Task.id == descendant_id)
        ).scalar_one_or_none()
        is not None
    ]
    # Now that the subtree is whole again, judge the series once against the
    # final effective state. Seeding every restored id keeps the fan-out the
    # per-row reconciles used to provide (ancestors and dependents included).
    reconcile(db, [root.id, *restored_ids])
    db.flush()
    db.refresh(root)
    return root, len(restored_ids)


def _deleted_subtree_depth_first(
    db: Session, task: Task, *, project_scoped: bool = True
) -> list[Task]:
    """The soft-deleted subtree rooted at ``task``, children before parents.

    Soft-deleting a parent cascade-soft-deletes its subtree, so the whole subtree
    sits in trash together; purging the root must take the descendants with it or
    they'd dangle a ``parent_task_id`` at a destroyed row. FK enforcement is on
    (``PRAGMA foreign_keys = ON``), so that dangle would *raise*; children-first
    ordering lets the caller delete in a single pass without tripping the
    self-referential FK.

    The walk is **scoped to the root's project** (BUG #189). Task hierarchies may
    cross project boundaries, so an unscoped walk let a purge reach out of the
    project the user selected and permanently destroy a trashed task owned by a
    different, still-active project — work that was independently restorable from
    that project's trash. A descendant in another project is left alone (and cut
    loose by ``purge_task``'s detach step, so no FK dangles); the walk stops
    there, since anything below it hangs off a row that survives.

    ``project_scoped=False`` drops that guard, and is **only** for internal
    corrections that undo a write this service made itself (BUG #212): the
    un-skip rewind must destroy the entire original skipped occurrence, whose
    descendants ``task_recurrence._clone_subtask_tree`` may have filed in other
    projects. Those rows have already been replaced by fresh clones, so leaving
    them detached in another project's trash strands an obsolete duplicate.
    Never pass it for a user-requested purge.
    """
    scope = task.project_id
    conditions = [Task.parent_task_id == task.id]
    if project_scoped:
        conditions.append(
            Task.project_id.is_(None) if scope is None else Task.project_id == scope
        )
    children = db.execute(deleted(Task).where(*conditions)).scalars().all()
    ordered: list[Task] = []
    for child in children:
        ordered.extend(
            _deleted_subtree_depth_first(db, child, project_scoped=project_scoped)
        )
    ordered.append(task)
    return ordered


def deleted_subtree_ids(db: Session, task: Task) -> list[int]:
    """Ids of every row ``purge_task(db, task)`` would destroy (``task`` included).

    Public so callers that need to *count* a purge's true reach before running it
    (see ``trash.purge_selected``) use the same traversal the purge itself does,
    instead of re-deriving it and drifting.
    """
    return [t.id for t in _deleted_subtree_depth_first(db, task)]


def log_task_purged(db: Session, task: Task) -> None:
    """Record the irreversible destruction of ``task`` in the audit log.

    Unlike ``tasks.log_task_event`` this does *not* skip unfiled tasks
    (``project_id is None``). That helper's silence is a feed-noise trade-off for
    reversible edits; a purge is the one mutation nothing can undo, so it is
    always written — with ``project_id=None`` it simply lives outside every
    per-project feed while still being durable history.

    ``activity_events.entity_id`` is a plain column, not a foreign key, so the
    event survives the row it describes. The title is snapshotted into the summary
    because after the purge nothing else remembers it.
    """
    activity.record_event(
        db,
        project_id=task.project_id,
        entity_type="task",
        entity_id=task.id,
        action="purged",
        summary=f'Task "{task.title}" permanently deleted',
    )


def log_task_detached_by_purge(
    db: Session, *, task_id: int, title: str, project_id: int | None, parent_title: str
) -> None:
    """Record that a purge cut ``task_id`` loose from a parent it destroyed.

    The action is ``"updated"`` — the documented set in ``ActivityEvent`` — because
    the row survives and what changed is one of its fields. ``"purged"`` would be a
    lie about a task that still exists, and a new action string would surface in the
    activity feed with no frontend handling. The summary carries the specifics.

    Like ``log_task_purged`` (and unlike ``tasks.log_task_event``) this does *not*
    skip unfiled tasks: the parent is gone forever, so this is the only record that
    the task was ever nested, and that is worth writing even where no per-project
    feed can show it.
    """
    activity.record_event(
        db,
        project_id=project_id,
        entity_type="task",
        entity_id=task_id,
        action="updated",
        summary=(
            f'Task "{title}" detached from permanently deleted '
            f'parent "{parent_title}"'
        ),
    )


@dataclass(frozen=True)
class _SeveredEdge:
    """One active dependency edge a purge destroys whose other endpoint lives on.

    ``survivor_waits`` says which side of the edge the surviving task was on:
    ``True`` it was the dependent (it loses something it was waiting on),
    ``False`` it was the blocker (it loses something that was waiting on it).
    """

    survivor_id: int
    survivor_title: str
    survivor_project_id: int | None
    destroyed_id: int
    survivor_waits: bool


def _severed_dependency_edges(db: Session, ids: Sequence[int]) -> list[_SeveredEdge]:
    """Active dependency edges a purge of ``ids`` destroys that a surviving task keeps.

    Both directions, because the edge is severed for whichever endpoint is left
    standing. Edges with *both* endpoints inside the purge set are excluded —
    nothing survives to record them against, and the pair's history is already
    covered by the two ``purged`` events.

    Already soft-deleted edges are excluded too: ``remove_dependency`` audited
    those when the user removed them, and the purge is only clearing a tombstone.

    Neither query filters ``Task.deleted_at``. A survivor may itself sit in trash —
    a cross-project row the scoped walk spares (BUG #189), or a task trashed on its
    own terms — and it is still restorable, so the edge it loses is still worth
    recording. Same reason ``remove_dependency`` looks its endpoints up including
    soft-deleted rows (issue #201).
    """
    dependents = db.execute(
        select(Task.id, Task.title, Task.project_id, TaskDependency.depends_on_task_id)
        .join(TaskDependency, TaskDependency.task_id == Task.id)
        .where(
            TaskDependency.deleted_at.is_(None),
            TaskDependency.depends_on_task_id.in_(ids),
            TaskDependency.task_id.not_in(ids),
        )
        .order_by(TaskDependency.id)
    ).all()
    blockers = db.execute(
        select(Task.id, Task.title, Task.project_id, TaskDependency.task_id)
        .join(TaskDependency, TaskDependency.depends_on_task_id == Task.id)
        .where(
            TaskDependency.deleted_at.is_(None),
            TaskDependency.task_id.in_(ids),
            TaskDependency.depends_on_task_id.not_in(ids),
        )
        .order_by(TaskDependency.id)
    ).all()
    return [
        _SeveredEdge(
            survivor_id=survivor_id,
            survivor_title=survivor_title,
            survivor_project_id=survivor_project_id,
            destroyed_id=destroyed_id,
            survivor_waits=survivor_waits,
        )
        for rows, survivor_waits in ((dependents, True), (blockers, False))
        for survivor_id, survivor_title, survivor_project_id, destroyed_id in rows
    ]


def log_dependency_severed_by_purge(
    db: Session,
    *,
    task_id: int,
    title: str,
    project_id: int | None,
    other_title: str,
    waits_on: bool,
) -> None:
    """Record that a purge destroyed the other end of one of ``task_id``'s edges.

    The action is ``"dependency_removed"`` and the dependent-side wording is
    ``remove_dependency``'s, so the feed reads the same whether the edge went away
    by hand or with the purge — it is the same event about the same edge. The
    blocker side has no manual counterpart to mirror (``remove_dependency`` only
    writes on the dependent), but losing a dependent is the same kind of
    structural change and the survivor is the only row left to record it against.

    "permanently deleted" is what the summary adds: nobody removed this
    dependency, a purge destroyed the task on the other end. That title is
    snapshotted here because afterwards it exists nowhere else — the same reason
    ``log_task_detached_by_purge`` snapshots the destroyed parent's.

    Like the other purge helpers (and unlike
    ``task_dependencies._log_dependency_event``) an unfiled survivor
    (``project_id is None``) is *not* skipped: no per-project feed can show the
    event, but it is the only surviving record that the edge ever existed.
    """
    relation = "no longer waits on" if waits_on else "no longer blocks"
    activity.record_event(
        db,
        project_id=project_id,
        entity_type="task",
        entity_id=task_id,
        action="dependency_removed",
        summary=f'Task "{title}" {relation} permanently deleted "{other_title}"',
    )


def purge_task(db: Session, task: Task, *, project_scoped: bool = True) -> None:
    """Permanently delete a trashed task and its soft-deleted subtree.

    Cleans the real FK edges first: dependency rows on either side of any subtree
    task, and any stray ``parent_task_id`` from a row outside the purge set (e.g. a
    child that was individually restored while its parent stayed in trash, or a
    trashed child owned by another project, which the scoped subtree walk leaves
    behind — see ``_deleted_subtree_depth_first``). Then the cascade markers: a
    surviving row's ``deleted_with_task_id`` naming a destroyed one is no FK the
    database would defend, but it must not outlive its target either (issue #251).
    The caller is responsible for committing.

    Every node in the subtree gets its own ``purged`` audit event *before* the
    row is destroyed — the audit trail must distinguish a restorable soft delete
    from permanent destruction, and cascade-purged descendants are as gone as the
    root the user actually clicked. Every *surviving* row the detach step
    reparents gets an ``updated`` event too (issue #242), and every surviving row
    the dependency cleanup severs an edge from gets a ``dependency_removed`` event
    (issue #254): both are structural changes to tasks that live on, and the
    destroyed endpoint's title exists nowhere else afterwards.

    ``project_scoped=False`` is reserved for internal corrections; see
    ``_deleted_subtree_depth_first``.
    """
    subtree = _deleted_subtree_depth_first(db, task, project_scoped=project_scoped)
    ids = [t.id for t in subtree]
    titles = {node.id: node.title for node in subtree}

    # Snapshotted before the delete erases the edges the audit has to describe —
    # the same shape as the detach step below: one unbounded bulk statement does
    # the write, the events are emitted from the snapshot, and the row change and
    # its history land in the same transaction (issue #254).
    #
    # No reconcile follows, unlike ``remove_dependency``: everything in the purge
    # set is already soft-deleted, and a soft-deleted blocker never blocked
    # (``effective_statuses`` omits it), so no survivor's effective status moves
    # here. This severs a stale edge; it does not unblock anything.
    severed = _severed_dependency_edges(db, ids)
    db.execute(
        sql_delete(TaskDependency).where(
            or_(
                TaskDependency.task_id.in_(ids),
                TaskDependency.depends_on_task_id.in_(ids),
            )
        )
    )
    for edge in severed:
        log_dependency_severed_by_purge(
            db,
            task_id=edge.survivor_id,
            title=edge.survivor_title,
            project_id=edge.survivor_project_id,
            other_title=titles[edge.destroyed_id],
            waits_on=edge.survivor_waits,
        )

    # Detach any row (active or not) still pointing into the purge set but not
    # itself being purged, so no dangling parent ref survives. Snapshotted first
    # because the update erases the very edge the audit event has to describe;
    # the bulk write stays a single statement (the fan-out is unbounded) and the
    # events are emitted from the snapshot in the same transaction, so the row
    # change and its history commit together or not at all.
    detached = db.execute(
        select(Task.id, Task.title, Task.project_id, Task.parent_task_id).where(
            Task.parent_task_id.in_(ids), Task.id.not_in(ids)
        )
    ).all()
    db.execute(
        update(Task)
        .where(Task.parent_task_id.in_(ids), Task.id.not_in(ids))
        .values(parent_task_id=None)
    )

    for detached_id, detached_title, detached_project_id, old_parent_id in detached:
        log_task_detached_by_purge(
            db,
            task_id=detached_id,
            title=detached_title,
            project_id=detached_project_id,
            parent_title=titles[old_parent_id],
        )

    # Same one-statement shape for the cascade markers of the rows that survive.
    # A marker naming a destroyed row is not inert: ``tasks.id`` is a plain rowid,
    # so the next insert is handed the freed id and ``_marked_descendant_ids``
    # then reads these rows as that new task's cascade, resurrecting another
    # project's trash as part of an unrelated undo (issue #251). Nothing to
    # snapshot or audit: unlike the parent detach this is internal restore
    # bookkeeping rather than user-visible state, which is why
    # ``projects.purge_project`` nulls the matching ``deleted_with_project_id``
    # markers silently too.
    db.execute(
        update(Task)
        .where(Task.deleted_with_task_id.in_(ids), Task.id.not_in(ids))
        .values(deleted_with_task_id=None)
    )

    for node in subtree:
        log_task_purged(db, node)

    for node in subtree:  # children before parents
        hard_delete(db, node)
