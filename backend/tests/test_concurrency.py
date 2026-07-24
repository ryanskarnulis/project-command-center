"""Two-connection races against invariants the service layer checks in Python.

These run on ``file_engine`` (a real file, WAL, NullPool) rather than the default
``:memory:`` + StaticPool fixture, because that fixture shares ONE DBAPI
connection — two "concurrent" sessions on it are literally the same transaction
and no race is reachable.

Making them deterministic in *both* directions takes some care, and two obvious
shapes do not work:

- "Both threads read, meet at a barrier, then both write" deadlocks on the fixed
  build. Preventing two writers from holding a transaction at once is the entire
  mechanism, so the second thread blocks at its first statement and never reaches
  the barrier.
- "Both threads start together and run freely" passes on the *broken* build. The
  threads do not reach their read at the same moment — connection setup alone put
  them ~80ms apart in practice — so the follower reads after the leader has
  already committed, and the race never materialises.

So each test uses a leader/follower pair with a one-way gate. ``_pace_reads``
makes the leader signal once its check-then-act read has answered and then dawdle
before writing; the follower waits for that signal before it even opens a
session. The gate is one-way and the follower's blocking happens in SQLite (under
``busy_timeout``), never on a Python barrier, which is what keeps the fixed build
from deadlocking:

- Before the fix the follower's read runs while the leader sleeps, sees nothing,
  and writes — so both write, every run.
- After the fix the follower blocks at ``BEGIN IMMEDIATE`` until the leader
  commits, then reads the committed truth and does the right thing.

Every test here was confirmed to fail on the previous code and pass on this one.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Project, Task, TaskDependency, TaskWorkflowStatus
from app.services import task_dependencies as deps_service
from app.services import task_recurrence as recurrence_service
from app.services import tasks as tasks_service

Race = Callable[..., tuple[tuple[Any, BaseException | None], ...]]

# Long enough that the follower's whole read-and-write comfortably fits inside it
# on the broken build; far short of the 5s busy_timeout the follower waits under
# on the fixed one.
LEAD_SECONDS = 0.2
GATE_TIMEOUT = 5.0


def _pace_reads(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str, gate: threading.Event
) -> None:
    """Let the leader's check-then-act read answer, then hold it open.

    Only the first caller through pays the delay; the follower runs at full speed
    so it races into the window rather than extending it.
    """
    original = getattr(module, name)

    def paced(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not gate.is_set():
            gate.set()
            time.sleep(LEAD_SECONDS)
        return result

    monkeypatch.setattr(module, name, paced)


def _writer(factory: sessionmaker[Session], engine: Engine) -> Session:
    """A session whose transaction begins IMMEDIATE, as write routes get."""
    conn = engine.connect()
    conn.info["begin_statement"] = "BEGIN IMMEDIATE"
    return factory(bind=conn)


def _seed_project(factory: sessionmaker[Session]) -> int:
    db = factory()
    project = Project(name="Races")
    db.add(project)
    db.commit()
    project_id = project.id
    db.close()
    return project_id


def _seed_tasks(
    factory: sessionmaker[Session], project_id: int, *titles: str
) -> list[int]:
    db = factory()
    tasks = [Task(project_id=project_id, title=title) for title in titles]
    db.add_all(tasks)
    db.commit()
    ids = [task.id for task in tasks]
    db.close()
    return ids


class TestConcurrentOccurrenceCreation:
    """Two completions of one recurring task must not fork the series."""

    def test_concurrent_completion_yields_exactly_one_live_occurrence(
        self,
        file_engine: Engine,
        session_factory: sessionmaker[Session],
        race: Race,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gate = threading.Event()
        _pace_reads(monkeypatch, recurrence_service, "find_live_occurrence_on", gate)

        project_id = _seed_project(session_factory)
        db = session_factory()
        head = Task(
            project_id=project_id,
            title="Standup",
            due_date=dt.date(2026, 7, 24),
            repeat_interval={"unit": "day", "every": 1},
            recurrence_id="series-race",
            workflow_status=TaskWorkflowStatus.done,
        )
        db.add(head)
        db.commit()
        head_id = head.id
        db.close()

        def complete() -> int:
            session = _writer(session_factory, file_engine)
            try:
                task = session.get(Task, head_id)
                assert task is not None
                spawned = recurrence_service.create_next_occurrence(session, task)
                session.commit()
                return int(spawned.id)
            finally:
                session.close()

        def leader(_barrier: Any) -> int:
            return complete()

        def follower(_barrier: Any) -> int:
            gate.wait(GATE_TIMEOUT)
            return complete()

        (first, first_exc), (second, second_exc) = race(leader, follower)

        assert first_exc is None, f"leader failed: {first_exc!r}"
        assert second_exc is None, f"follower failed: {second_exc!r}"
        # The loser is idempotent, not an error: both callers get the same row.
        assert first == second, f"series forked into {first} and {second}"

        db = session_factory()
        live = db.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.recurrence_id == "series-race",
                Task.due_date == dt.date(2026, 7, 25),
                Task.deleted_at.is_(None),
            )
        ).scalar_one()
        db.close()
        assert live == 1, f"series forked: {live} live occurrences on 2026-07-25"


class TestConcurrentDependencyCreation:
    """add_dependency's duplicate and cycle checks are reads, and reads go stale."""

    def test_duplicate_edge_is_a_domain_error_never_an_integrity_error(
        self,
        file_engine: Engine,
        session_factory: sessionmaker[Session],
        race: Race,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gate = threading.Event()
        # _would_cycle is add_dependency's LAST read before the insert. Pacing an
        # earlier read is useless: the guards that follow it re-read, and outside a
        # held transaction those reads see the other writer's commit and correctly
        # reject. The window that actually matters is the one between the final
        # check and the write.
        _pace_reads(monkeypatch, deps_service, "_would_cycle", gate)

        project_id = _seed_project(session_factory)
        task_id, blocker_id = _seed_tasks(session_factory, project_id, "A", "B")

        def add() -> str:
            session = _writer(session_factory, file_engine)
            try:
                deps_service.add_dependency(session, task_id, blocker_id)
                session.commit()
                return "created"
            finally:
                session.close()

        def leader(_barrier: Any) -> str:
            return add()

        def follower(_barrier: Any) -> str:
            gate.wait(GATE_TIMEOUT)
            return add()

        outcomes = race(leader, follower)
        results = [result for result, _ in outcomes]
        errors = [exc for _, exc in outcomes]

        assert results.count("created") == 1, f"expected one winner, got {results}"
        loser = next(exc for exc in errors if exc is not None)
        # A bare IntegrityError reaching the route is a 500; a duplicate is a 409.
        assert isinstance(loser, deps_service.DuplicateDependencyError), repr(loser)
        assert not isinstance(loser, IntegrityError)

        db = session_factory()
        edges = db.execute(
            select(func.count())
            .select_from(TaskDependency)
            .where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_task_id == blocker_id,
                TaskDependency.deleted_at.is_(None),
            )
        ).scalar_one()
        db.close()
        assert edges == 1

    def test_opposing_edges_cannot_both_commit(
        self,
        file_engine: Engine,
        session_factory: sessionmaker[Session],
        race: Race,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A->B and B->A concurrently used to produce a permanent deadlock.

        Both cycle checks passed on stale reads and both committed. After that
        is_blocked was true on both tasks forever — neither could ever be
        completed — and _would_cycle rejected any edge that might repair it, so
        the only escape was removing an edge, which is not something a user would
        think to try. No database constraint can express acyclicity, so this one
        rests entirely on writers being serialized.
        """
        gate = threading.Event()
        # The last read before the insert — see the duplicate-edge test above.
        _pace_reads(monkeypatch, deps_service, "_would_cycle", gate)

        project_id = _seed_project(session_factory)
        left, right = _seed_tasks(session_factory, project_id, "A", "B")

        def link(a: int, b: int) -> str:
            session = _writer(session_factory, file_engine)
            try:
                deps_service.add_dependency(session, a, b)
                session.commit()
                return "created"
            finally:
                session.close()

        def leader(_barrier: Any) -> str:
            return link(left, right)

        def follower(_barrier: Any) -> str:
            gate.wait(GATE_TIMEOUT)
            return link(right, left)

        outcomes = race(leader, follower)
        results = [result for result, _ in outcomes]
        errors = [exc for _, exc in outcomes]

        assert results.count("created") == 1, (
            f"both directions committed — the graph now holds a cycle: {results}"
        )
        loser = next(exc for exc in errors if exc is not None)
        assert isinstance(loser, deps_service.DependencyError), repr(loser)

        db = session_factory()
        try:
            # The invariant that actually matters to a user.
            assert not (
                deps_service.is_blocked(db, left) and deps_service.is_blocked(db, right)
            ), "both tasks block each other and neither can ever be completed"
            edges = db.execute(
                select(func.count())
                .select_from(TaskDependency)
                .where(TaskDependency.deleted_at.is_(None))
            ).scalar_one()
            assert edges == 1
        finally:
            db.close()


class TestConcurrentCompletionGate:
    """Completion is gated on a read of whether the task is blocked."""

    def test_completing_a_task_while_it_gains_a_blocker(
        self,
        file_engine: Engine,
        session_factory: sessionmaker[Session],
        race: Race,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A done-yet-blocked task is a contradiction both guards exist to prevent.

        add_dependency refuses to add a blocker to a finished task, and the
        completion gate refuses to finish a blocked one. Run concurrently on stale
        reads, both checks pass and the result is exactly the state each forbids.
        """
        gate = threading.Event()
        _pace_reads(monkeypatch, deps_service, "blocked_task_ids", gate)

        project_id = _seed_project(session_factory)
        task_id, blocker_id = _seed_tasks(session_factory, project_id, "Ship", "Review")

        def leader(_barrier: Any) -> str:
            session = _writer(session_factory, file_engine)
            try:
                task = tasks_service.get_task(session, task_id)
                assert task is not None
                tasks_service.mark_done(session, task)
                session.commit()
                return "completed"
            finally:
                session.close()

        def follower(_barrier: Any) -> str:
            gate.wait(GATE_TIMEOUT)
            session = _writer(session_factory, file_engine)
            try:
                deps_service.add_dependency(session, task_id, blocker_id)
                session.commit()
                return "blocked"
            finally:
                session.close()

        race(leader, follower)

        db = session_factory()
        try:
            status = deps_service.effective_statuses(db, [task_id]).get(task_id)
            blocked = deps_service.is_blocked(db, task_id)
            assert not (blocked and status == TaskWorkflowStatus.done), (
                "task ended up both done and blocked"
            )
        finally:
            db.close()
