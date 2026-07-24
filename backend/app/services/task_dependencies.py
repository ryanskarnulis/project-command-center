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


def _depends_on_ids(db: Session, task_id: int) -> list[int]:
    return [dep.depends_on_task_id for dep in list_dependencies(db, task_id)]


def _would_cycle(db: Session, task_id: int, depends_on_id: int) -> bool:
    """True if adding ``task_id -> depends_on_id`` closes a cycle.

    The edge is safe unless ``depends_on_id`` can already reach ``task_id`` by
    following existing ``depends_on`` edges (then ``task_id`` would transitively
    depend on itself). DFS from ``depends_on_id``; a visited set bounds the walk
    even if the stored graph is already corrupt.
    """
    stack = [depends_on_id]
    visited: set[int] = set()
    while stack:
        current = stack.pop()
        if current == task_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        stack.extend(_depends_on_ids(db, current))
    return False


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
    deps_of: dict[int, list[int]] = {}
    closure: set[int] = set()
    frontier = set(ids)
    while frontier:
        closure |= frontier
        nxt: set[int] = set()
        for tid in frontier:
            edges = _depends_on_ids(db, tid)
            deps_of[tid] = edges
            nxt.update(d for d in edges if d not in closure)
        frontier = nxt

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
    statuses = effective_statuses(db, _depends_on_ids(db, task_id))
    return any(status != TaskWorkflowStatus.done for status in statuses.values())


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


def edges_among_tasks(db: Session, task_ids: Sequence[int]) -> list[TaskDependency]:
    """Active edges whose *both* endpoints are in ``task_ids`` (one query).

    Scoped to the supplied task set so the planning payload only draws links
    between tasks it actually renders (a dependency pointing outside the project's
    not-done set has no bar to attach to). Ordered by id for stability.
    """
    ids = set(task_ids)
    if not ids:
        return []
    return list(
        db.execute(
            active(TaskDependency)
            .where(TaskDependency.task_id.in_(ids))
            .where(TaskDependency.depends_on_task_id.in_(ids))
            .order_by(TaskDependency.id)
        )
        .scalars()
        .all()
    )


def top_level_blocker_counts(db: Session, task_ids: Sequence[int]) -> dict[int, int]:
    """Top-level blockers in ``task_ids`` mapped to downstream blocked counts.

    A top-level blocker is active, unfinished, has unfinished downstream work
    waiting on it, and is not itself waiting on another unfinished dependency.
    Counts are transitive: in ``A depends on B depends on C``, only ``C`` is
    returned, with a count of 2.

    "Unfinished" is the effective status on both endpoints, resolved in Python
    rather than filtered in SQL — see ``effective_statuses``.
    """
    requested = set(task_ids)
    if not requested:
        return {}

    dependent = aliased(Task)
    blocker = aliased(Task)
    rows = db.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
        .join(dependent, dependent.id == TaskDependency.task_id)
        .join(blocker, blocker.id == TaskDependency.depends_on_task_id)
        .where(
            TaskDependency.deleted_at.is_(None),
            dependent.deleted_at.is_(None),
            blocker.deleted_at.is_(None),
        )
    ).all()

    statuses = effective_statuses(
        db, (int(task_id) for row in rows for task_id in row)
    )

    def _unfinished(task_id: int) -> bool:
        return statuses.get(task_id) not in (None, TaskWorkflowStatus.done)

    dependencies_by_task: defaultdict[int, set[int]] = defaultdict(set)
    dependents_by_blocker: defaultdict[int, set[int]] = defaultdict(set)
    for dependent_id, blocker_id in rows:
        if not _unfinished(int(dependent_id)) or not _unfinished(int(blocker_id)):
            continue
        dependencies_by_task[int(dependent_id)].add(int(blocker_id))
        dependents_by_blocker[int(blocker_id)].add(int(dependent_id))

    blocked_ids = set(dependencies_by_task)
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
