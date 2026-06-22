from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency, TaskReviewStatus
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service

# One responsibility: the per-project planning logic — assembling the payload
# (tasks + edges) and computing the dependency auto-shift. Bar *geometry* (pixel
# placement) stays in the frontend; the *scheduling* (which dates move when a task
# moves) lives here, not the frontend (CLAUDE.md prime directive #1).

MINUTES_PER_DAY = 480  # 8h * 60 — mirrors ganttModel.MINUTES_PER_DAY


def gantt_tasks(db: Session, project_id: int) -> Sequence[Task]:
    """Accepted, not-done tasks for the project (subtasks included)."""
    return tasks_service.list_tasks(
        db,
        project_id,
        review_status=TaskReviewStatus.accepted,
        exclude_done=True,
    )


def gantt_dependencies(
    db: Session, tasks: Sequence[Task]
) -> list[TaskDependency]:
    """Active edges between the given tasks (both endpoints in the set)."""
    return deps_service.edges_among_tasks(db, [t.id for t in tasks])


# --- Dependency auto-shift (Slice 5) ---------------------------------------
#
# Pure core: given each task's placement (scheduled_start + estimate) and the
# finish-to-start edges between them, when one task moves, push downstream
# dependents forward just enough that none starts on or before a blocker finishes
# (the same rule the frontend's `computeViolations` detects, generalized to a
# cascade). The function below is side-effect free and unit-tested in isolation;
# `cascade_downstream` is the DB-facing wrapper that loads state, runs it, and
# applies the new `scheduled_start` values.


@dataclass(frozen=True)
class Placement:
    """A task's schedule inputs for the shift: its start and span in whole days.

    ``scheduled_start`` is ``None`` for an unscheduled task — it has no bar, so it
    neither moves nor anchors a downstream constraint (mirrors the frontend, where
    an unscheduled task lives in the side bucket, not on the timeline).
    """

    scheduled_start: date | None
    estimated_minutes: int | None

    @property
    def span_days(self) -> int:
        """Whole calendar days the bar spans. Always at least 1 (mirrors ``spanDays``)."""
        minutes = self.estimated_minutes or 0
        return max(1, math.ceil(minutes / MINUTES_PER_DAY))

    def end(self) -> date | None:
        """Inclusive last day, or ``None`` when unscheduled."""
        if self.scheduled_start is None:
            return None
        return self.scheduled_start + timedelta(days=self.span_days - 1)


def _topological_order(edges: Sequence[tuple[int, int]]) -> list[int]:
    """Blockers before dependents, over the ``(dependent, blocker)`` edges.

    Kahn's algorithm. The stored graph is acyclic (enforced when an edge is added),
    but if a cycle somehow survives, its tasks are simply dropped from the order so
    the cascade still terminates rather than looping.
    """
    blockers_of: defaultdict[int, set[int]] = defaultdict(set)
    dependents_of: defaultdict[int, set[int]] = defaultdict(set)
    nodes: set[int] = set()
    for dependent, blocker in edges:
        nodes.add(dependent)
        nodes.add(blocker)
        blockers_of[dependent].add(blocker)
        dependents_of[blocker].add(dependent)

    ready = sorted(n for n in nodes if not blockers_of[n])
    order: list[int] = []
    remaining = {n: set(blockers_of[n]) for n in nodes}
    while ready:
        node = ready.pop(0)
        order.append(node)
        for dependent in sorted(dependents_of[node]):
            remaining[dependent].discard(node)
            if not remaining[dependent]:
                ready.append(dependent)
                ready.sort()
    return order


def compute_shifts(
    placements: Mapping[int, Placement],
    edges: Sequence[tuple[int, int]],
) -> dict[int, date]:
    """New ``scheduled_start`` per task that must move forward to honor its edges.

    ``edges`` are ``(dependent_id, blocker_id)`` finish-to-start links. Walking the
    graph blockers-first, each *scheduled* dependent must start no earlier than
    ``blocker.end + 1`` for every *scheduled* blocker; if it currently starts
    earlier, it shifts to the latest such required day (and that new end propagates
    to its own dependents). Returns only the tasks whose start actually changes —
    a task already scheduled late enough is left untouched, and unscheduled tasks
    (no ``scheduled_start``) neither move nor constrain anyone.
    """
    blockers_of: defaultdict[int, list[int]] = defaultdict(list)
    for dependent, blocker in edges:
        blockers_of[dependent].append(blocker)

    # Working copy of each task's end day, updated as upstream tasks shift so the
    # constraint a dependent sees reflects already-applied moves.
    ends: dict[int, date | None] = {
        task_id: placement.end() for task_id, placement in placements.items()
    }
    shifts: dict[int, date] = {}

    for task_id in _topological_order(edges):
        placement = placements.get(task_id)
        if placement is None or placement.scheduled_start is None:
            continue  # unscheduled: nothing to move
        earliest_start: date | None = None
        for blocker_id in blockers_of[task_id]:
            blocker_end = ends.get(blocker_id)
            if blocker_end is None:
                continue  # unscheduled blocker imposes no finish-to-start anchor
            required = blocker_end + timedelta(days=1)
            if earliest_start is None or required > earliest_start:
                earliest_start = required
        if earliest_start is None or earliest_start <= placement.scheduled_start:
            continue  # already starts late enough
        shifts[task_id] = earliest_start
        ends[task_id] = earliest_start + timedelta(days=placement.span_days - 1)

    return shifts


@dataclass(frozen=True)
class Override:
    """A staged, unsaved change to one task's placement for a what-if preview.

    Either field may be ``None`` to mean "leave the stored value"; an explicit
    ``scheduled_start``/``estimated_minutes`` replaces it for the hypothetical run.
    Mirrors the two placement fields the task PATCH accepts, so committing a
    what-if is just firing those PATCHes (each of which cascades for real).
    """

    task_id: int
    scheduled_start: date | None = None
    estimated_minutes: int | None = None


def preview_shifts(
    db: Session, project_id: int, overrides: Sequence[Override]
) -> dict[int, date]:
    """Hypothetical ``scheduled_start`` per task under a set of staged overrides.

    Loads the project's real planning tasks + edges, layers the overrides onto
    their placements *in memory only*, and runs the same pure ``compute_shifts``
    the committed path uses — but writes nothing (no flush, no commit). The result
    is the full new start for every task that ends up at a different day than its
    stored value: both the directly-overridden tasks and the downstream dependents
    the cascade pushes. A task whose override leaves it where it already was, or
    that the cascade doesn't touch, is omitted (the frontend keeps its real bar).

    This is the read-side twin of ``cascade_downstream``: same rules, same engine,
    no persistence (CLAUDE.md prime directive #1 — the scheduling math is Python,
    and a what-if must reuse it rather than re-deriving dates in the frontend).
    """
    tasks = gantt_tasks(db, project_id)
    edges = gantt_dependencies(db, tasks)
    override_by_id = {o.task_id: o for o in overrides}

    placements: dict[int, Placement] = {}
    for task in tasks:
        override = override_by_id.get(task.id)
        if override is None:
            placements[task.id] = Placement(
                task.scheduled_start, task.estimated_minutes
            )
            continue
        placements[task.id] = Placement(
            override.scheduled_start
            if override.scheduled_start is not None
            else task.scheduled_start,
            override.estimated_minutes
            if override.estimated_minutes is not None
            else task.estimated_minutes,
        )

    edge_pairs = [(edge.task_id, edge.depends_on_task_id) for edge in edges]
    shifts = compute_shifts(placements, edge_pairs)

    # The overridden start is part of the preview too: ``compute_shifts`` only
    # reports tasks the *cascade* moves, but a directly-staged start that differs
    # from the stored value must also surface. Merge it in (the cascade's value
    # wins if an override target is itself pushed further by an upstream blocker).
    result = dict(shifts)
    for task_id, placement in placements.items():
        override = override_by_id.get(task_id)
        if (
            override is not None
            and override.scheduled_start is not None
            and task_id not in result
            and placement.scheduled_start is not None
        ):
            result[task_id] = placement.scheduled_start
    return result


def cascade_downstream(db: Session, project_id: int, changed_task: Task) -> list[int]:
    """Shift downstream dependents after ``changed_task`` moved; return shifted ids.

    Loads the project's planning tasks + edges, runs the pure ``compute_shifts``
    over their current placements, and writes the new ``scheduled_start`` onto each
    task that must move (in one transaction with the caller's change — the route
    commits). The triggering task is never itself in the result: the cascade only
    pushes work that depends on it. A no-op when nothing downstream conflicts.
    """
    tasks = gantt_tasks(db, project_id)
    edges = gantt_dependencies(db, tasks)
    placements = {
        task.id: Placement(task.scheduled_start, task.estimated_minutes)
        for task in tasks
    }
    shifts = compute_shifts(
        placements,
        [(edge.task_id, edge.depends_on_task_id) for edge in edges],
    )
    by_id = {task.id: task for task in tasks}
    for task_id, new_start in shifts.items():
        by_id[task_id].scheduled_start = new_start
    if shifts:
        db.flush()
    return sorted(shifts)
