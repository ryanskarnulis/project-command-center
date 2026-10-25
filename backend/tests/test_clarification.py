"""Typed clarification: ``ask_user`` and the open question it leaves (#420).

The planner asks with candidate ids; the registry checks they exist; a landed
ask ends the run and the narrator asks, naming the candidates in order. The
next run's state block carries the question until a write lands or a
candidate moves.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import clarification, state_block
from app.ai.clarification import Candidate, OpenQuestion
from app.ai.loop import AgentLoop, ToolCallRecord
from app.api import routes_agent
from app.db.models import Task
from app.main import app
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.tools import registry, runtime
from app.tools.refusals import ToolRefusal
from tests.scripted_provider import ScriptedProvider
from tests.scripted_provider import text_turn as _text
from tests.scripted_provider import tool_calls_turn as _calls

QUESTION = "Which one — booking the appointment, or paying the bill?"


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def _seed_dentists(factory: sessionmaker[Session]) -> tuple[int, int]:
    with factory() as db:
        home = projects_service.create_project(db, name="Home")
        book = tasks_service.create_task(db, project_id=home.id, title="Book dentist appointment")
        pay = tasks_service.create_task(db, project_id=home.id, title="Pay the dentist bill")
        db.commit()
        return book.id, pay.id


# --- the tool ---------------------------------------------------------------------


def test_ask_user_answers_the_candidates_in_order(tool_db: sessionmaker[Session]) -> None:
    book, pay = _seed_dentists(tool_db)
    rows = registry.call_tool("ask_user", {"task_ids": [pay, book]}, actor="agent:loop")
    assert [row.id for row in rows] == [pay, book]


def test_ask_user_refuses_an_id_that_names_nothing(tool_db: sessionmaker[Session]) -> None:
    book, _ = _seed_dentists(tool_db)
    with pytest.raises(ToolRefusal):
        registry.call_tool("ask_user", {"task_ids": [book, 9999]}, actor="agent:loop")


def test_ask_user_refuses_a_repeated_candidate(tool_db: sessionmaker[Session]) -> None:
    book, _ = _seed_dentists(tool_db)
    with pytest.raises(ToolRefusal):
        registry.call_tool("ask_user", {"task_ids": [book, book]}, actor="agent:loop")


def test_ask_user_needs_two_candidates(tool_db: sessionmaker[Session]) -> None:
    book, _ = _seed_dentists(tool_db)
    with pytest.raises(ValidationError):
        registry.call_tool("ask_user", {"task_ids": [book]}, actor="agent:loop")


def test_ask_user_is_the_loops_alone() -> None:
    assert "ask_user" in {spec.name for spec in registry.tool_specs()}
    assert "ask_user" not in {tool.name for tool in registry.mcp_tools()}
    assert "ask_user" in registry.read_only_tool_names()


# --- the run ----------------------------------------------------------------------


def test_a_landed_ask_ends_the_run_on_the_question(tool_db: sessionmaker[Session]) -> None:
    book, pay = _seed_dentists(tool_db)
    provider = ScriptedProvider(
        [
            _calls(("ask_user", {"task_ids": [book, pay]}), ("complete_task", {"task_id": book})),
            _text("never reached"),
        ],
        narrations=[_text(QUESTION)],
    )
    result = AgentLoop(provider).run("Mark the dentist one as done.")

    assert result.stop_reason == "completed"
    assert result.reply == QUESTION
    assert result.note is None
    # The call after the ask never started; nothing changed.
    assert [(r.tool, r.result is not None) for r in result.tool_calls] == [
        ("ask_user", True),
        ("complete_task", False),
    ]
    assert result.tool_calls[1].error is not None
    assert result.tool_calls[1].error.startswith("not run: ")
    with tool_db() as db:
        assert set(db.scalars(select(Task.workflow_status)).all()) == {"open"}
    # One planner turn, then the narrator, told to name both in order.
    assert len(provider.requests) == 2
    brief = provider.requests[-1]["messages"][1]["content"]
    assert "The run ended on a question" in brief
    assert brief.index("1. Book dentist appointment") < brief.index("2. Pay the dentist bill")


def test_a_refused_ask_does_not_end_the_run(tool_db: sessionmaker[Session]) -> None:
    book, _ = _seed_dentists(tool_db)
    provider = ScriptedProvider(
        [_calls(("ask_user", {"task_ids": [book, 9999]})), _text("Couldn't tell.")],
        narrations=[_text("Which one?")],
    )
    result = AgentLoop(provider).run("Mark the dentist one as done.")
    # The refusal went back to the planner, which went on to its note.
    assert result.note == "Couldn't tell."
    assert len(provider.requests) == 3


def test_a_failed_narration_asks_in_codes_words(tool_db: sessionmaker[Session]) -> None:
    book, pay = _seed_dentists(tool_db)
    provider = ScriptedProvider(
        [_calls(("ask_user", {"task_ids": [pay, book]}))], narrations=[_text("")]
    )
    result = AgentLoop(provider).run("Mark the dentist one as done.")
    assert result.reply == (
        "Which task did you mean?\n1. Pay the dentist bill\n2. Book dentist appointment"
    )


def test_a_delegate_is_handed_the_question(tool_db: sessionmaker[Session]) -> None:
    """A delegate (#482) gets the ask in the handoff, candidates in order, and
    no narrator call."""
    book, pay = _seed_dentists(tool_db)
    provider = ScriptedProvider([_calls(("ask_user", {"task_ids": [book, pay]}))])
    result = AgentLoop(provider).run("Mark the dentist one as done.", actor="agent:conductor")
    assert len(provider.requests) == 1
    assert (result.reply or "").endswith(
        "Ask the user which one they meant:\n1. Book dentist appointment\n2. Pay the dentist bill"
    )


# --- the open question ------------------------------------------------------------


def _ask_turn(*candidates: tuple[int, str, str]) -> dict[str, Any]:
    rows = [{"id": i, "title": t, "updated_at": u} for i, t, u in candidates]
    record = ToolCallRecord(
        tool="ask_user",
        arguments={"task_ids": [c[0] for c in candidates]},
        result=json.dumps(rows),
    )
    return {"role": "assistant", "content": QUESTION, "tool_calls": [record.model_dump()]}


def _write_turn() -> dict[str, Any]:
    record = ToolCallRecord(
        tool="complete_task", arguments={"task_id": 5}, result=json.dumps({"id": 5})
    )
    return {"role": "assistant", "content": "Done.", "tool_calls": [record.model_dump()]}


def _read_turn() -> dict[str, Any]:
    record = ToolCallRecord(tool="list_tasks", arguments={}, result=json.dumps({"items": []}))
    return {"role": "assistant", "content": "Here.", "tool_calls": [record.model_dump()]}


_ASKED = [
    {"role": "user", "content": "Mark the dentist one as done."},
    _ask_turn((4, "Book dentist appointment", "t1"), (5, "Pay the dentist bill", "t2")),
]


def test_the_newest_ask_stays_open_through_reads_and_chat() -> None:
    history = [*_ASKED, {"role": "user", "content": "Which is due sooner?"}, _read_turn()]
    question, closed = clarification.from_history(history)
    assert closed == ""
    assert question == OpenQuestion(
        "Mark the dentist one as done.",
        (Candidate(4, "Book dentist appointment", "t1"), Candidate(5, "Pay the dentist bill", "t2")),
    )


def test_a_later_write_closes_the_question() -> None:
    history = [*_ASKED, {"role": "user", "content": "The second one."}, _write_turn()]
    assert clarification.from_history(history) == (None, clarification.ANSWERED_OR_MOVED_ON)


def test_no_ask_no_question() -> None:
    assert clarification.from_history([{"role": "user", "content": "hi"}]) == (None, "")


def test_a_candidate_trashed_or_edited_since_closes_it() -> None:
    question, _ = clarification.from_history(_ASKED)
    assert question is not None
    assert clarification.staleness(question, {4: "t1", 5: "t2"}) == ""
    assert clarification.staleness(question, {4: "t1"}) == clarification.CANDIDATE_GONE
    assert clarification.staleness(question, {4: "t1", 5: "t9"}) == clarification.CANDIDATE_CHANGED


def test_still_open_checks_live_rows(tool_db: sessionmaker[Session]) -> None:
    book, pay = _seed_dentists(tool_db)
    rows = registry.call_tool("ask_user", {"task_ids": [book, pay]}, actor="agent:loop")
    found = (OpenQuestion("x", clarification.candidates_of(json.dumps(
        [row.model_dump(mode="json") for row in rows]
    ))), "")
    assert state_block.still_open(found)[0] is not None

    with tool_db() as db:
        task = tasks_service.get_task(db, pay)
        assert task is not None
        tasks_service.update_task(db, task, {"title": "Pay it"})
        db.commit()
    assert state_block.still_open(found) == (None, clarification.CANDIDATE_CHANGED)


def test_the_next_run_carries_the_question_and_acts_on_the_answer(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    """Through the route: the ask is saved with its turn, the next planner
    reads it numbered in its state block, and its answer is a normal write."""
    home = client.post("/api/projects", json={"name": "Home"}).json()["id"]
    book, pay = (
        client.post("/api/tasks", json={"project_id": home, "title": title}).json()["id"]
        for title in ("Book dentist appointment", "Pay the dentist bill")
    )
    provider = ScriptedProvider(
        [
            _calls(("ask_user", {"task_ids": [book, pay]})),
            _calls(("complete_task", {"task_id": pay})),
            _text("Completed the bill."),
            _text("Nothing."),
        ],
        narrations=[_text(QUESTION), _text("Done — paid."), _text("Okay.")],
    )
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]
    url = f"/api/agent/conversations/{conversation_id}/messages"

    first = client.post(url, json={"content": "Mark the dentist one as done."}).json()
    assert first["assistant_message"]["content"] == QUESTION

    client.post(url, json={"content": "The second one."})
    opening = provider.requests[2]["messages"][1]["content"]
    assert "Open question:" in opening
    assert f"1. id {book} · Book dentist appointment" in opening
    assert f"2. id {pay} · Pay the dentist bill" in opening
    assert client.get(f"/api/tasks/{pay}").json()["workflow_status"] == "done"
    assert client.get(f"/api/tasks/{book}").json()["workflow_status"] == "open"

    # Answered: the run after carries no question.
    client.post(url, json={"content": "Thanks."})
    opening = next(r for r in reversed(provider.requests) if r["tools"])["messages"][1]["content"]
    assert "Open question:" not in opening
