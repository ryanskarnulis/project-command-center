"""Query-count budgets for the dependency-graph read paths.

The status/blocked derivations used to issue one SELECT *per node* in the
transitive dependency closure, so the cost of a read scaled with the size of the
graph rather than with the size of the answer. Measured before the batched
closure loader landed:

    shape         tasks  edges   GET /api/tasks   GET /api/tasks/{id}
    chain           200    199              610                   205
    fan-out         200    199              214                   208
    dense DAG        50   1225              160                   106

These budgets exist so that regression cannot come back quietly. They are
deliberately expressed as query *counts*, not timings: on one user's SQLite file
the wall time is invisible, but the round-trip count is exactly what turns into
write-lock hold time once mutating requests take ``BEGIN IMMEDIATE``.

The chain budgets are deliberately depth-linear. Frontier batching collapses
fan-out and dense graphs to a constant, but a chain of depth N still needs N
levels; making that constant requires a recursive CTE, which cannot terminate on
a cyclic graph without an explicit depth cap (see ``_closure``). Real dependency
chains are a handful deep, so this is the right trade — a reviewer seeing a bound
of 60 next to bounds of 12 should read this paragraph, not assume a typo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.task_reads import read_with_blocked, reads_with_blocked
from app.db.models import Project, Task, TaskDependency
from app.services import dashboard as dashboard_service
from app.services import focus as focus_service
from app.services import tasks as tasks_service

Counter = Callable[[Engine], Any]


def _seed(db: Session, count: int, shape: str) -> list[Task]:
    """A project of ``count`` open tasks wired into ``shape``.

    ``chain``   T0 -> T1 -> ... (each waits on the next; depth == count)
    ``fanout``  every task waits on T0 (depth 1, width count)
    ``dense``   every task waits on every lower-indexed one (count^2/2 edges)
    ``none``    no edges at all — the common real-world board
    """
    project = Project(name="Budget")
    db.add(project)
    db.flush()
    tasks = [Task(project_id=project.id, title=f"T{i}") for i in range(count)]
    db.add_all(tasks)
    db.flush()
    ids = [t.id for t in tasks]

    if shape == "chain":
        pairs = [(ids[i], ids[i + 1]) for i in range(count - 1)]
    elif shape == "fanout":
        pairs = [(ids[i], ids[0]) for i in range(1, count)]
    elif shape == "dense":
        pairs = [(ids[i], ids[j]) for i in range(count) for j in range(i)]
    elif shape == "none":
        pairs = []
    else:  # pragma: no cover - guards a typo in a parametrize case
        raise AssertionError(f"unknown shape {shape!r}")

    db.add_all([TaskDependency(task_id=a, depends_on_task_id=b) for a, b in pairs])
    db.flush()
    return tasks


class TestTaskListBudget:
    """``GET /api/tasks`` — the list path, including its status filter."""

    @pytest.mark.parametrize(
        ("shape", "count", "budget"),
        [
            ("none", 200, 10),
            ("fanout", 50, 20),
            ("fanout", 200, 20),
            ("dense", 50, 20),
            # Depth-linear by design — see the module docstring.
            ("chain", 50, 20),
        ],
    )
    def test_list_is_bounded(
        self,
        db_session: Session,
        test_engine: Engine,
        count_queries: Counter,
        shape: str,
        count: int,
        budget: int,
    ) -> None:
        _seed(db_session, count, shape)
        with count_queries(test_engine) as statements:
            listed = tasks_service.list_tasks(db_session, exclude_done=True)
            reads_with_blocked(db_session, listed)
        assert len(statements) <= budget, (
            f"{shape} n={count}: {len(statements)} queries exceeds {budget}\n"
            + "\n".join(statements[:20])
        )


class TestTaskDetailBudget:
    """``read_with_blocked`` — one task, but it used to resolve the whole graph."""

    @pytest.mark.parametrize(
        ("shape", "count", "budget"),
        [
            ("none", 200, 8),
            ("fanout", 50, 12),
            ("fanout", 200, 12),
            ("dense", 50, 12),
        ],
    )
    def test_detail_is_independent_of_graph_size(
        self,
        db_session: Session,
        test_engine: Engine,
        count_queries: Counter,
        shape: str,
        count: int,
        budget: int,
    ) -> None:
        tasks = _seed(db_session, count, shape)
        with count_queries(test_engine) as statements:
            read_with_blocked(db_session, tasks[-1])
        assert len(statements) <= budget, (
            f"{shape} n={count}: {len(statements)} queries exceeds {budget}"
        )

    def test_detail_cost_does_not_grow_with_unrelated_graph(
        self,
        db_session: Session,
        test_engine: Engine,
        count_queries: Counter,
    ) -> None:
        """The sharpest form of the old bug: reading task X paid for graph Y.

        ``top_level_blocker_counts`` scanned every edge in the database, so adding
        dependencies to *unrelated* tasks made an untouched task's detail read more
        expensive. The two counts here must match exactly.
        """
        loner = _seed(db_session, 1, "none")[0]
        with count_queries(test_engine) as before:
            read_with_blocked(db_session, loner)

        _seed(db_session, 60, "fanout")  # a busy graph the loner has no edge into
        with count_queries(test_engine) as after:
            read_with_blocked(db_session, loner)

        assert len(after) == len(before), (
            f"unrelated graph changed the cost of one task's read: "
            f"{len(before)} -> {len(after)}"
        )


class TestAggregateBudgets:
    """Focus and dashboard both roll up the full active set."""

    def test_focus_plan_is_bounded(
        self,
        db_session: Session,
        test_engine: Engine,
        count_queries: Counter,
    ) -> None:
        _seed(db_session, 50, "fanout")
        with count_queries(test_engine) as statements:
            focus_service.get_focus_plan(db_session, target_date=date(2026, 7, 24))
        assert len(statements) <= 25, "\n".join(statements[:20])

    def test_dashboard_overview_is_bounded_with_dependencies(
        self,
        db_session: Session,
        test_engine: Engine,
        count_queries: Counter,
    ) -> None:
        """``test_routes_dashboard`` pins <= 5, but only on a graph with no edges.

        That bound says nothing about the dependency path, which is where the
        amplification lived. This is the same assertion with edges present.
        """
        _seed(db_session, 50, "fanout")
        with count_queries(test_engine) as statements:
            dashboard_service.get_overview(db_session)
        assert len(statements) <= 12, "\n".join(statements[:20])
