from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.db.models import (
    Task,
    TaskDependency,
    TaskReviewStatus,
    TaskWorkflowStatus,
)
from app.services.common import active, soft_delete
from app.services.tasks import get_task


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


def add_dependency(
    db: Session, task_id: int, depends_on_id: int
) -> TaskDependency:
    """Record that ``task_id`` must wait for ``depends_on_id`` to be done.

    Rejects self-edges, duplicates of an active edge, edges referencing a
    missing/soft-deleted task, and any edge that would create a cycle.
    """
    if task_id == depends_on_id:
        raise SelfDependencyError("A task cannot depend on itself")
    if get_task(db, task_id) is None or get_task(db, depends_on_id) is None:
        raise DependencyError("Both tasks must exist")
    if depends_on_id in _depends_on_ids(db, task_id):
        raise DuplicateDependencyError("That dependency already exists")
    if _would_cycle(db, task_id, depends_on_id):
        raise DependencyCycleError("That dependency would create a cycle")

    edge = TaskDependency(task_id=task_id, depends_on_task_id=depends_on_id)
    db.add(edge)
    db.flush()
    db.refresh(edge)
    return edge


def get_dependency(db: Session, dependency_id: int) -> TaskDependency | None:
    return db.execute(
        active(TaskDependency).where(TaskDependency.id == dependency_id)
    ).scalar_one_or_none()


def remove_dependency(db: Session, edge: TaskDependency) -> None:
    soft_delete(edge)
    db.flush()


def is_blocked(db: Session, task_id: int) -> bool:
    """True if any active dependency's depended-on task is not workflow-done."""
    for dep in list_dependencies(db, task_id):
        depended = get_task(db, dep.depends_on_task_id)
        if depended is not None and depended.workflow_status != TaskWorkflowStatus.done:
            return True
    return False


def blocked_task_ids(db: Session, task_ids: Sequence[int]) -> set[int]:
    """The subset of ``task_ids`` that have an unfinished dependency.

    One query for the whole list (avoids N+1 on the task list): a task is blocked
    if it has an active edge to an active, not workflow-``done`` task.
    """
    if not task_ids:
        return set()
    depended = aliased(Task)
    rows = db.execute(
        select(TaskDependency.task_id)
        .join(depended, depended.id == TaskDependency.depends_on_task_id)
        .where(
            TaskDependency.deleted_at.is_(None),
            TaskDependency.task_id.in_(task_ids),
            depended.deleted_at.is_(None),
            depended.workflow_status != TaskWorkflowStatus.done,
        )
        .distinct()
    ).scalars()
    return set(rows)


def top_level_blocker_counts(db: Session, task_ids: Sequence[int]) -> dict[int, int]:
    """Top-level blockers in ``task_ids`` mapped to downstream blocked counts.

    A top-level blocker is active, accepted, unfinished, has unfinished accepted
    downstream work waiting on it, and is not itself waiting on another unfinished
    dependency. Counts are transitive: in ``A depends on B depends on C``, only
    ``C`` is returned, with a count of 2.
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
            dependent.review_status == TaskReviewStatus.accepted,
            dependent.workflow_status != TaskWorkflowStatus.done,
            blocker.deleted_at.is_(None),
            blocker.review_status == TaskReviewStatus.accepted,
            blocker.workflow_status != TaskWorkflowStatus.done,
        )
    ).all()

    dependencies_by_task: defaultdict[int, set[int]] = defaultdict(set)
    dependents_by_blocker: defaultdict[int, set[int]] = defaultdict(set)
    for dependent_id, blocker_id in rows:
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
