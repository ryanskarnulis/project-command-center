from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.db.models import (
    Task,
    TaskDependency,
    TaskWorkflowStatus,
)
from app.services import activity
from app.services.common import active, soft_delete
from app.services.tasks import compute_rollups, get_task


class DependencyError(ValueError):
    """A dependency edge can't be added (self, duplicate, or it would cycle).

    The caller surfaces a 409. Subclasses distinguish the reasons for clearer
    messages, but the route handles them uniformly.
    """


class SelfDependencyError(DependencyError):
    """A task cannot depend on itself."""


class DuplicateDependencyError(DependencyError):
    """That dependency edge already exists (active)."""


class DependencyCycleError(DependencyError):
    """Adding the edge would create a cycle (e.g. A->B->A) — a deadlock."""


class CompletedTaskDependencyError(DependencyError):
    """A dependency was added to a task that is already (effectively) done.

    Adding a blocker to a finished task would leave it done-yet-blocked — a
    contradiction. A blocked task can never have been completed (completion is
    gated), so this only rejects a genuinely finished dependent.
    """


def list_dependencies(db: Session, task_id: int) -> Sequence[TaskDependency]:
    """Active edges where ``task_id`` is the dependent (what it waits on)."""
    return (
        db.execute(
            active(TaskDependency)
            .where(TaskDependency.task_id == task_id)
            .order_by(TaskDependency.id)
        )
        .scalars()
        .all()
    )


def list_dependents(db: Session, task_id: int) -> Sequence[TaskDependency]:
    """Active edges where ``task_id`` is depended on (what waits on it)."""
    return (
        db.execute(
            active(TaskDependency)
            .where(TaskDependency.depends_on_task_id == task_id)
            .order_by(TaskDependency.id)
        )
        .scalars()
        .all()
    )


def list_dependencies_for(
    db: Session, task_ids: Iterable[int]
) -> dict[int, list[TaskDependency]]:
    """Batched ``list_dependencies``: active edges per task, in one query.

    For callers rendering many tasks' blockers at once (the focus plan's blocked
    rows), where asking per task is an N+1.
    """
    ids = sorted(set(task_ids))
    if not ids:
        return {}
    by_task: dict[int, list[TaskDependency]] = {tid: [] for tid in ids}
    for chunk in _chunked(ids):
        edges = (
            db.execute(
                active(TaskDependency)
                .where(TaskDependency.task_id.in_(chunk))
                .order_by(TaskDependency.id)
            )
            .scalars()
            .all()
        )
        for edge in edges:
            by_task[edge.task_id].append(edge)
    return by_task


def _depends_on_ids(db: Session, task_id: int) -> list[int]:
    return [dep.depends_on_task_id for dep in list_dependencies(db, task_id)]


# SQLite's bound-parameter ceiling is 32766 on modern builds but 999 on older
# ones, and the closure walks feed whole frontiers into an ``IN``. Chunking at
# 900 keeps the query legal everywhere for the cost of five lines.
_IN_CHUNK = 900


def _chunked(ids: Sequence[int], size: int = _IN_CHUNK) -> Iterable[Sequence[int]]:
    for start in range(0, len(ids), size):
        yield ids[start : start + size]


def _closure(
    db: Session, task_ids: Iterable[int], *, reverse: bool
) -> dict[int, list[int]]:
    """Adjacency over the transitive closure of ``task_ids``, one query per level.

    ``reverse=False`` follows ``depends_on`` (what a task waits on);
    ``reverse=True`` follows it backwards (what waits on a task). Either way the
    walk is frontier-batched — one ``WHERE ... IN (...)`` per *level* rather than
    one query per node, which is what the per-node ``_depends_on_ids`` loop this
    replaces did. That turns O(nodes) round trips into O(depth).

    Every walked node gets a key, empty list included, so callers can tell
    "walked, has no edges" from "never walked". Ordered by edge id, matching
    ``list_dependencies``, because ``effective_statuses``' resolve loop breaks on
    the first unfinished blocker.

    Deliberately does NOT filter ``Task.deleted_at``: the walk traverses *through*
    soft-deleted tasks and they are excluded later, when the closure is loaded via
    ``active(Task)``. Filtering here would change which nodes reach
    ``compute_rollups`` — see ``effective_statuses``.

    A ``WITH RECURSIVE`` CTE would collapse the remaining O(depth) to one query,
    but SQLite's recursive CTE does not terminate on a cyclic graph unless it
    carries a depth or path column, and a cycle is reachable here (concurrent
    opposing ``add_dependency`` calls can both pass the cycle check). If you take
    that route, the depth cap is not optional.
    """
    src = TaskDependency.depends_on_task_id if reverse else TaskDependency.task_id
    dst = TaskDependency.task_id if reverse else TaskDependency.depends_on_task_id

    adjacency: dict[int, list[int]] = {}
    frontier = set(task_ids)
    while frontier:
        for tid in frontier:
            adjacency.setdefault(tid, [])
        rows: list[tuple[int, int]] = []
        ordered = sorted(frontier)
        for chunk in _chunked(ordered):
            rows.extend(
                (int(from_id), int(to_id))
                for from_id, to_id in db.execute(
                    select(src, dst)
                    .where(TaskDependency.deleted_at.is_(None), src.in_(chunk))
                    .order_by(TaskDependency.id)
                ).all()
            )
        nxt: set[int] = set()
        for from_id, to_id in rows:
            adjacency[int(from_id)].append(int(to_id))
            if int(to_id) not in adjacency:
                nxt.add(int(to_id))
        frontier = nxt
    return adjacency


def _dependency_closure(db: Session, task_ids: Iterable[int]) -> dict[int, list[int]]:
    """``task_id -> [depends_on_task_id]`` over the transitive dependency closure."""
    return _closure(db, task_ids, reverse=False)


def _dependent_closure(db: Session, task_ids: Iterable[int]) -> dict[int, list[int]]:
    """``blocker_id -> [dependent_id]`` over everything transitively waiting on ``task_ids``."""
    return _closure(db, task_ids, reverse=True)


def _direct_dependencies(
    db: Session, task_ids: Iterable[int]
) -> dict[int, list[int]]:
    """``task_id -> [depends_on_task_id]``, one hop only, in one query."""
    ordered = sorted(set(task_ids))
    adjacency: dict[int, list[int]] = {tid: [] for tid in ordered}
    for chunk in _chunked(ordered):
        rows = db.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
            .where(
                TaskDependency.deleted_at.is_(None),
                TaskDependency.task_id.in_(chunk),
            )
            .order_by(TaskDependency.id)
        ).all()
        for task_id, blocker_id in rows:
            adjacency[int(task_id)].append(int(blocker_id))
    return adjacency


def _would_cycle(db: Session, task_id: int, depends_on_id: int) -> bool:
    """True if adding ``task_id -> depends_on_id`` closes a cycle.

    The edge is safe unless ``depends_on_id`` can already reach ``task_id`` by
    following existing ``depends_on`` edges (then ``task_id`` would transitively
    depend on itself). The closure walk is frontier-batched and its own
    already-walked set bounds it even if the stored graph is already corrupt.
    """
    return task_id in _dependency_closure(db, [depends_on_id])


def _log_dependency_event(
    db: Session, task: Task, other: Task, action: str, summary: str
) -> None:
    """Record a dependency change on the dependent task's project feed.

    Mirrors ``tasks.log_task_event``'s unfiled rule: a task with no project has
    no feed to show the event on, so nothing is recorded.
    """
    if task.project_id is None:
        return
    activity.record_event(
        db,
        project_id=task.project_id,
        entity_type="task",
        entity_id=task.id,
        action=action,
        summary=summary.format(task=task.title, other=other.title),
    )


def add_dependency(
    db: Session, task_id: int, depends_on_id: int
) -> TaskDependency:
    """Record that ``task_id`` must wait for ``depends_on_id`` to be done.

    Rejects self-edges, duplicates of an active edge, edges referencing a
    missing/soft-deleted task, and any edge that would create a cycle.
    """
    if task_id == depends_on_id:
        raise SelfDependencyError("A task cannot depend on itself")
    task = get_task(db, task_id)
    depended = get_task(db, depends_on_id)
    if task is None or depended is None:
        raise DependencyError("Both tasks must exist")
    if effective_statuses(db, [task_id]).get(task_id) == TaskWorkflowStatus.done:
        raise CompletedTaskDependencyError(
            "Can't add a dependency to a task that is already done"
        )
    if depends_on_id in _depends_on_ids(db, task_id):
        raise DuplicateDependencyError("That dependency already exists")
    if _would_cycle(db, task_id, depends_on_id):
        raise DependencyCycleError("That dependency would create a cycle")

    edge = TaskDependency(task_id=task_id, depends_on_task_id=depends_on_id)
    db.add(edge)
    db.flush()
    db.refresh(edge)
    _log_dependency_event(
        db, task, depended, "dependency_added", 'Task "{task}" now waits on "{other}"'
    )
    return edge


def get_dependency(db: Session, dependency_id: int) -> TaskDependency | None:
    return db.execute(
        active(TaskDependency).where(TaskDependency.id == dependency_id)
    ).scalar_one_or_none()


def remove_dependency(db: Session, edge: TaskDependency) -> None:
    soft_delete(edge)
    db.flush()
    task = get_task(db, edge.task_id)
    depended = get_task(db, edge.depends_on_task_id)
    if task is not None and depended is not None:
        _log_dependency_event(
            db,
            task,
            depended,
            "dependency_removed",
            'Task "{task}" no longer waits on "{other}"',
        )
    # Dropping the last blocker of an all-children-done recurring checklist is a
    # transition into effective completion just as much as completing that blocker
    # would have been — the series has to roll forward either way. Local import:
    # task_recurrence builds on this module, so a top-level import would cycle.
    from app.services import task_recurrence

    task_recurrence.reconcile(db, [edge.task_id])


def effective_statuses(
    db: Session, task_ids: Iterable[int]
) -> dict[int, TaskWorkflowStatus]:
    """Blocked-aware, rolled-up workflow status per active task id.

    Two derivations stack here:

    1. A checklist parent's status is rolled up from its children and never
       written back (see ``tasks.compute_rollups``), so comparing the stored
       column would strand a fully-done checklist as a permanent blocker — and,
       conversely, treat a stored-done row that gained an active child as a
       satisfied one.
    2. A rolled-up ``done`` is capped to ``in_progress`` when the task itself has
       an unfinished (transitive) dependency. A checklist parent's completion is
       *derived* from its children and so never passes through the blocked-gate
       that leaf completion does; without this, finishing a blocked parent's
       children would silently satisfy anything waiting on the parent. Leaves are
       usually kept out of this case by the completion gate and the
       ``add_dependency`` guard, but not always: a leaf completed while its
       blocker sat in the trash (so it read as not-blocked) becomes
       stored-``done``-and-blocked again once the blocker is restored, and the cap
       correctly demotes it here too. The cap therefore applies uniformly to
       leaves and checklist parents alike.

    The dependency graph is a DAG (``add_dependency`` rejects cycles); the visited
    memo bounds the walk even against a corrupt graph. Every dependency check
    resolves through here so it asks the same question the read model answers.
    Missing/soft-deleted ids are absent from the result.
    """
    ids = set(task_ids)
    if not ids:
        return {}

    # Gather the transitive dependency closure: demoting a rolled-up-done task
    # needs each of its (transitive) blockers' effective status too, and a blocker
    # may sit outside the requested ids.
    deps_of = _dependency_closure(db, ids)
    closure = set(deps_of)

    tasks = db.execute(active(Task).where(Task.id.in_(closure))).scalars().all()
    rollups = compute_rollups(db, tasks)
    rollup_status = {t.id: rollups[t.id].workflow_status for t in tasks}

    memo: dict[int, TaskWorkflowStatus] = {}

    def resolve(tid: int) -> TaskWorkflowStatus | None:
        if tid in memo:
            return memo[tid]
        status = rollup_status.get(tid)
        if status is None:  # missing/soft-deleted: never a blocker
            return None
        memo[tid] = status  # tentative — bounds a cyclic (corrupt) graph
        if status == TaskWorkflowStatus.done:
            for dep_id in deps_of.get(tid, ()):
                dep_status = resolve(dep_id)
                if dep_status is not None and dep_status != TaskWorkflowStatus.done:
                    status = TaskWorkflowStatus.in_progress
                    break
        memo[tid] = status
        return status

    resolved = {tid: resolve(tid) for tid in ids}
    return {tid: status for tid, status in resolved.items() if status is not None}


def is_blocked(db: Session, task_id: int) -> bool:
    """True if any active dependency's depended-on task is not effectively done."""
    return task_id in blocked_task_ids(db, [task_id])


def blocked_task_ids(db: Session, task_ids: Sequence[int]) -> set[int]:
    """The subset of ``task_ids`` that have an unfinished dependency.

    One query for the whole list (avoids N+1 on the task list): a task is blocked
    if it has an active edge to an active task that is not *effectively* done. The
    done check can't be a SQL predicate — see ``effective_statuses``.
    """
    if not task_ids:
        return set()
    depended = aliased(Task)
    rows = db.execute(
        select(TaskDependency.task_id, depended.id)
        .join(depended, depended.id == TaskDependency.depends_on_task_id)
        .where(
            TaskDependency.deleted_at.is_(None),
            TaskDependency.task_id.in_(task_ids),
            depended.deleted_at.is_(None),
        )
        .distinct()
    ).all()
    statuses = effective_statuses(db, (blocker_id for _, blocker_id in rows))
    return {
        int(dependent_id)
        for dependent_id, blocker_id in rows
        if statuses.get(int(blocker_id)) != TaskWorkflowStatus.done
    }


def top_level_blocker_counts(db: Session, task_ids: Sequence[int]) -> dict[int, int]:
    """Top-level blockers in ``task_ids`` mapped to downstream blocked counts.

    A top-level blocker is active, unfinished, has unfinished downstream work
    waiting on it, and is not itself waiting on another unfinished dependency.
    Counts are transitive: in ``A depends on B depends on C``, only ``C`` is
    returned, with a count of 2.

    "Unfinished" is the effective status on both endpoints, resolved in Python
    rather than filtered in SQL — see ``effective_statuses``.

    Both walks are scoped to ``task_ids``. This previously scanned *every* active
    edge in the database regardless of the argument, so resolving a single task's
    read model pulled in the whole global dependency graph.

    Soft-deleted endpoints need no SQL filter: ``effective_statuses`` omits them,
    so ``_unfinished`` reads ``None`` and drops the edge exactly as the old
    ``deleted_at IS NULL`` joins did.
    """
    requested = set(task_ids)
    if not requested:
        return {}

    # Everything transitively waiting on the requested tasks — the population the
    # transitive count walks over.
    downstream = _dependent_closure(db, requested)
    # One hop upstream is enough to answer "is this task itself waiting on
    # something unfinished?", which is what disqualifies it from being *top-level*.
    upstream = _direct_dependencies(db, requested)
    if not any(downstream.values()) and not any(upstream.values()):
        # Nothing waits on these tasks and they wait on nothing: no top-level
        # blockers by definition. Worth the early return — a board with no
        # dependencies at all is the common case, and this keeps it off the
        # status-resolution path entirely.
        return {}

    statuses = effective_statuses(
        db,
        set(downstream)
        | {tid for ids in downstream.values() for tid in ids}
        | {tid for ids in upstream.values() for tid in ids},
    )

    def _unfinished(task_id: int) -> bool:
        return statuses.get(task_id) not in (None, TaskWorkflowStatus.done)

    dependents_by_blocker: defaultdict[int, set[int]] = defaultdict(set)
    for blocker_id, dependent_ids in downstream.items():
        if not _unfinished(blocker_id):
            continue
        for dependent_id in dependent_ids:
            if _unfinished(dependent_id):
                dependents_by_blocker[blocker_id].add(dependent_id)

    blocked_ids = {
        task_id
        for task_id, blocker_ids in upstream.items()
        if _unfinished(task_id) and any(_unfinished(b) for b in blocker_ids)
    }
    counts: dict[int, int] = {}
    for task_id in requested:
        if task_id in blocked_ids or task_id not in dependents_by_blocker:
            continue
        seen: set[int] = set()
        stack = list(dependents_by_blocker[task_id])
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(dependents_by_blocker.get(current, ()))
        if seen:
            counts[task_id] = len(seen)
    return counts
