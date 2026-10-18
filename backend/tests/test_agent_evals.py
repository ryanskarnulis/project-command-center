"""Agent eval harness: scripted scenarios against the REAL model (loop epic, slice 4).

Opt-in like the provider's live smoke — skipped unless ``PCC_AGENT_EVALS=1``,
so CI and default local runs never touch the GPU:

    cd backend
    PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s

Each scenario seeds a fresh in-memory DB through the service layer, runs one
user ask through the full ``AgentLoop`` + shared tool registry against the
live runtime (``LLAMACPP_BASE_URL``, default :8200 gemma-4-12b), then asserts
two things:

* **trajectory shape** — behavioral, not exact sequences (the model is
  nondeterministic): the right tool family was used, reads preceded writes,
  nothing mutated on read-only asks;
* **DB end-state** — the mutation really happened (or really didn't),
  soft-delete/audit invariants included.

This suite is the tripwire from ``docs/agent-design.md``: baseline results
are recorded there, and a regression here is what would ever justify
revisiting the model choice or adding embeddings. ``-s`` shows the per-run
``[eval] …`` stats lines the baseline table is built from. The first call may
cold-load the model (~100 s); everything after runs warm.
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator, Sequence
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.loop import (
    DELEGATE_ACTORS,
    LOOP_ACTOR,
    AgentLoop,
    AgentRunResult,
    ToolCallRecord,
)
from app.ai.providers.llamacpp import provider_from_settings
from app.api import routes_agent
from app.db.models import ActivityEvent, Project, Task, TaskWorkflowStatus
from app.main import app
from app.services import projects as projects_service
from app.services import task_trash
from app.services import tasks as tasks_service
from app.tools import runtime

pytestmark = pytest.mark.skipif(
    os.environ.get("PCC_AGENT_EVALS") != "1",
    reason="agent evals run the real model: set PCC_AGENT_EVALS=1",
)

# Tool-name prefixes that never write; everything else counts as a mutation.
_READ_PREFIXES = ("list_", "get_")


def _is_read(tool: str) -> bool:
    return tool.startswith(_READ_PREFIXES) or tool == "search"


def _mutations(result: AgentRunResult) -> list[ToolCallRecord]:
    return [record for record in result.tool_calls if not _is_read(record.tool)]


def _successful(result: AgentRunResult, tool: str) -> list[ToolCallRecord]:
    return [
        record
        for record in result.tool_calls
        if record.tool == tool and record.error is None
    ]


@pytest.fixture
def eval_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    """Point the loop's per-tool-call sessions at this test's fresh DB."""
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def _report(scenario: str, result: AgentRunResult, duration: float) -> None:
    """The ``[eval]`` stats line the baseline table is built from."""
    trajectory = " → ".join(
        record.tool + ("!" if record.error is not None else "")
        for record in result.tool_calls
    )
    print(
        f"\n[eval] scenario={scenario} stop={result.stop_reason} "
        f"iterations={result.iterations} duration={duration:.1f}s "
        f"trajectory=[{trajectory}]"
    )


def _run(scenario: str, prompt: str, *, max_iterations: int = 10) -> AgentRunResult:
    """One eval run straight through the loop, reported."""
    started = time.monotonic()
    with provider_from_settings() as provider:
        result = AgentLoop(provider, max_iterations=max_iterations).run(prompt)
    _report(scenario, result, time.monotonic() - started)
    return result


class _RecordingLoop(AgentLoop):
    """The real loop, keeping each run's actor + result for the asserts/stats.

    The route never returns ``AgentRunResult`` (iterations, stop reason), so the
    delegate scenario captures it here instead of reconstructing it.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.runs: list[tuple[str, AgentRunResult]] = []

    def run(
        self,
        user_message: str,
        *,
        history: Sequence[dict[str, Any]] | None = None,
        actor: str = LOOP_ACTOR,
        deadline: float | None = None,
    ) -> AgentRunResult:
        result = super().run(
            user_message, history=history, actor=actor, deadline=deadline
        )
        self.runs.append((actor, result))
        return result


def _event_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(ActivityEvent)).scalar_one()


# --- Scenarios ----------------------------------------------------------------


def test_eval_create_task_with_fields(eval_db: sessionmaker[Session]) -> None:
    """Create with every field: title, project routing, priority, date math."""
    with eval_db() as db:
        errands = projects_service.create_project(db, name="Errands")
        errands_id = errands.id
        db.commit()

    result = _run(
        "create_task_with_fields",
        'Create a task "Buy stamps" in the Errands project, high priority, due tomorrow.',
    )

    assert result.stop_reason == "completed"
    assert _successful(result, "create_task"), "expected a successful create_task"
    with eval_db() as db:
        task = db.execute(
            select(Task).where(Task.deleted_at.is_(None))
        ).scalar_one()
        assert "stamp" in task.title.lower()
        assert task.project_id == errands_id
        assert task.priority.value == "high"
        assert task.due_date == date.today() + timedelta(days=1)
        events = db.execute(
            select(ActivityEvent).where(ActivityEvent.entity_type == "task")
        ).scalars().all()
        assert events and all(e.actor == LOOP_ACTOR for e in events)


def test_eval_find_and_complete(eval_db: sessionmaker[Session]) -> None:
    """Retrieval tripwire: the task is described, not named — look it up, then act."""
    with eval_db() as db:
        home = projects_service.create_project(db, name="Home")
        for title in (
            "Fix the leaking kitchen tap",
            "Rake the leaves",
            "Book dentist appointment",
        ):
            tasks_service.create_task(db, project_id=home.id, title=title)
        db.commit()

    result = _run("find_and_complete", "Mark the task about the dentist as done.")

    assert result.stop_reason == "completed"
    completes = _successful(result, "complete_task")
    assert completes, "expected a successful complete_task"
    # A read (search/list/get) must have located the task before the write.
    tools = [record.tool for record in result.tool_calls]
    first_complete = tools.index("complete_task")
    assert any(_is_read(tool) for tool in tools[:first_complete]), (
        f"no read before complete_task in {tools}"
    )
    with eval_db() as db:
        tasks = {
            t.title: t.workflow_status
            for t in db.execute(select(Task)).scalars().all()
        }
        assert tasks["Book dentist appointment"] == TaskWorkflowStatus.done
        assert tasks["Fix the leaking kitchen tap"] == TaskWorkflowStatus.open
        assert tasks["Rake the leaves"] == TaskWorkflowStatus.open


def test_eval_reschedule(eval_db: sessionmaker[Session]) -> None:
    """Targeted field update: change the due date, touch nothing else."""
    with eval_db() as db:
        work = projects_service.create_project(db, name="Work")
        task = tasks_service.create_task(
            db,
            project_id=work.id,
            title="Quarterly report",
            due_date=date(2026, 7, 15),
        )
        task_id = task.id
        db.commit()

    result = _run(
        "reschedule",
        "Change the due date of the quarterly report to July 20.",
    )

    assert result.stop_reason == "completed"
    assert _successful(result, "update_task"), "expected a successful update_task"
    with eval_db() as db:
        updated = db.get(Task, task_id)
        assert updated is not None
        assert updated.due_date == date(2026, 7, 20)
        assert updated.workflow_status == TaskWorkflowStatus.open
        assert updated.title == "Quarterly report"


def test_eval_delete_is_soft_and_restorable(eval_db: sessionmaker[Session]) -> None:
    """The only delete the agent has is the trash; the row must be restorable."""
    with eval_db() as db:
        general = projects_service.create_project(db, name="Scratch")
        task = tasks_service.create_task(
            db, project_id=general.id, title="Old draft"
        )
        task_id = task.id
        db.commit()

    result = _run("delete_is_soft", 'Delete the task "Old draft".')

    assert result.stop_reason == "completed"
    assert _successful(result, "trash_task"), "expected a successful trash_task"
    with eval_db() as db:
        # Gone from the active set, present in the trash, restorable.
        assert db.execute(
            select(Task).where(Task.deleted_at.is_(None))
        ).scalar_one_or_none() is None
        trashed = task_trash.get_deleted_task(db, task_id)
        assert trashed is not None and trashed.deleted_at is not None
        deleted_events = db.execute(
            select(ActivityEvent).where(ActivityEvent.action == "deleted")
        ).scalars().all()
        assert deleted_events and all(e.actor == LOOP_ACTOR for e in deleted_events)


def test_eval_read_only_question_mutates_nothing(
    eval_db: sessionmaker[Session],
) -> None:
    """A question is answered with reads: correct number, zero writes."""
    with eval_db() as db:
        reading = projects_service.create_project(db, name="Reading")
        for title in ("Dune", "Middlemarch", "The Windup Bird Chronicle"):
            tasks_service.create_task(db, project_id=reading.id, title=title)
        done = tasks_service.create_task(db, project_id=reading.id, title="Emma")
        tasks_service.mark_done(db, done)
        db.commit()
        events_before = _event_count(db)

    result = _run(
        "read_only_count",
        "How many open tasks are in the Reading project? Reply with just the number.",
    )

    assert result.stop_reason == "completed"
    assert result.reply is not None and "3" in result.reply
    assert _mutations(result) == [], (
        f"read-only ask must not mutate: {[r.tool for r in _mutations(result)]}"
    )
    with eval_db() as db:
        assert _event_count(db) == events_before


def test_eval_honest_about_missing_task(eval_db: sessionmaker[Session]) -> None:
    """A target that doesn't exist must not be invented or acted on."""
    with eval_db() as db:
        projects_service.create_project(db, name="Hangar")
        db.commit()
        events_before = _event_count(db)

    # Observed baseline behavior: gemma re-checks exhaustively (search, lists,
    # per-project lists, even the trash) before conceding — up to 10 turns.
    # Give the honest concession headroom; the honesty asserts below are the
    # point of the scenario, not the search-budget frugality.
    result = _run(
        "honest_about_missing",
        'Mark the task "Launch the zeppelin" as done.',
        max_iterations=14,
    )

    assert result.stop_reason == "completed"
    assert result.reply, "expected a text reply explaining the situation"
    # Nothing was completed, created, or otherwise written.
    assert not _successful(result, "complete_task")
    assert not _successful(result, "create_task")
    with eval_db() as db:
        assert (
            db.execute(select(func.count()).select_from(Task)).scalar_one() == 0
        )
        assert _event_count(db) == events_before
        # And no project was invented either.
        assert (
            db.execute(select(func.count()).select_from(Project)).scalar_one() == 1
        )


def test_eval_delegate_actor_attribution(
    client: TestClient, db_session: Session, eval_db: sessionmaker[Session]
) -> None:
    """A conductor-delegated run is audited as the conductor, end to end (#328).

    Driven through ``POST /api/agent/conversations/{id}/messages`` with the
    ``X-Agent-Actor`` header — the only place a delegate identity enters — so
    header → ``resolve_actor`` → loop → registry → ``activity_events`` is
    exercised against the real model, not just a scripted provider.

    Once the client is in play, reads go through ``db_session`` (the session
    the client's routes use): the test engine is one shared StaticPool
    connection, and a second session would BEGIN inside its open transaction.
    """
    delegate = "agent:conductor"
    assert delegate in DELEGATE_ACTORS
    with eval_db() as db:
        home = projects_service.create_project(db, name="Home")
        home_id = home.id
        db.commit()

    with provider_from_settings() as provider:
        loop = _RecordingLoop(provider)
        app.dependency_overrides[routes_agent.get_agent_loop] = lambda: loop
        # A user-made conversation (no header): its own audit row stays the user's.
        conversation_id = client.post("/api/agent/conversations", json={}).json()[
            "id"
        ]
        last_event_before = db_session.execute(
            select(func.max(ActivityEvent.id))
        ).scalar_one()
        db_session.rollback()  # end the read; the run's tool sessions share the connection

        started = time.monotonic()
        response = client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            json={"content": 'Create a task "Call the plumber" in the Home project.'},
            headers={"X-Agent-Actor": delegate},
        )
        duration = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert len(loop.runs) == 1
    actor, result = loop.runs[0]
    _report("delegate_actor", result, duration)
    # The header was resolved to the delegate before the loop ran.
    assert actor == delegate
    assert result.stop_reason == "completed"
    assert _successful(result, "create_task"), "expected a successful create_task"
    # The persisted assistant turn is the run's trajectory, as returned.
    assistant = response.json()["assistant_message"]
    assert assistant["stop_reason"] == "completed"
    assert [c["tool"] for c in assistant["tool_calls"] or []] == [
        r.tool for r in result.tool_calls
    ]

    task = db_session.execute(
        select(Task).where(Task.deleted_at.is_(None))
    ).scalar_one()
    assert "plumber" in task.title.lower()
    assert task.project_id == home_id
    # Every event the run wrote is the delegate's — never the loop's own
    # identity, the MCP default, or the (null) user.
    run_events = db_session.execute(
        select(ActivityEvent).where(ActivityEvent.id > last_event_before)
    ).scalars().all()
    assert run_events, "the run wrote no activity events"
    assert all(e.actor == delegate for e in run_events), [
        (e.entity_type, e.action, e.actor) for e in run_events
    ]
    assert any(
        e.entity_type == "task" and e.action == "created" and e.entity_id == task.id
        for e in run_events
    )
    # Writes outside the delegated run are not re-attributed: the
    # conversation's creation (and the seed) stay the user's.
    prior = db_session.execute(
        select(ActivityEvent).where(ActivityEvent.id <= last_event_before)
    ).scalars().all()
    assert prior and all(e.actor is None for e in prior)
