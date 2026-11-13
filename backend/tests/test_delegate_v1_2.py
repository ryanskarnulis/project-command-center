"""Delegate API v1.2 (#527): ``context``, ``mode: "read"`` and ``needs_input``.

The contract is ``../agent-standard/delegate-api.md`` "v1.2", decided in
conductor#145; these pin PCC's side of it at the route, over a scripted
provider.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import delegate_context
from app.ai.loop import AgentLoop
from app.api import routes_agent
from app.db.models import ConversationMessage, ConversationRole, Task
from app.main import app
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.tools import runtime
from tests.scripted_provider import ScriptedProvider, text_turn, tool_calls_turn

HONEY = {"app": "music", "kind": "album", "id": "mbid-1", "title": "Honey", "untrusted": True}


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def _use_loop(provider: ScriptedProvider) -> None:
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)


def _thread(client: TestClient) -> int:
    return int(client.post("/api/agent/conversations", json={}).json()["id"])


def _post(
    client: TestClient,
    conversation_id: int,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json=body,
        headers=headers or {},
    )


def _seed_dentists(factory: sessionmaker[Session]) -> tuple[int, int]:
    with factory() as db:
        home = projects_service.create_project(db, name="Home")
        book = tasks_service.create_task(db, project_id=home.id, title="Book dentist appointment")
        pay = tasks_service.create_task(db, project_id=home.id, title="Pay the dentist bill")
        db.commit()
        return book.id, pay.id


def _planner_turn(provider: ScriptedProvider) -> str:
    return str(provider.requests[0]["messages"][1]["content"])


# --- context ---------------------------------------------------------------------


def test_context_reaches_the_planner_after_the_words_as_data(
    client: TestClient, db_session: Session
) -> None:
    provider = ScriptedProvider([text_turn("Noted.")])
    _use_loop(provider)
    conversation_id = _thread(client)
    context = {
        "refs": [{"app": "tasks", "kind": "task", "id": "41", "title": "Pay rent"}, HONEY],
        "facts": ["I sort music on Sundays"],
    }

    response = _post(
        client, conversation_id, {"content": "anything due for this?", "context": context}
    )

    assert response.status_code == 200
    turn = _planner_turn(provider)
    words = turn.index("anything due for this?")
    given = turn.index(delegate_context.section(context) or "")
    assert words < given
    # Our own ref carries the id we can act on; another app's is a name.
    assert "- task 41 “Pay rent”" in turn
    assert "- music album “Honey” (outside text)" in turn
    assert "mbid-1" not in turn
    assert "- the person said before: “I sort music on Sundays”" in turn
    stored = db_session.execute(
        select(ConversationMessage).where(ConversationMessage.role == ConversationRole.user)
    ).scalar_one()
    assert stored.context == {
        "refs": [
            {
                "app": "tasks",
                "kind": "task",
                "id": "41",
                "title": "Pay rent",
                "untrusted": False,
                "picked": False,
            },
            {**HONEY, "picked": False},
        ],
        "facts": ["I sort music on Sundays"],
    }
    assert stored.content == "anything due for this?"


def test_a_message_without_context_reads_as_before(client: TestClient) -> None:
    provider = ScriptedProvider([text_turn("Hi.")])
    _use_loop(provider)

    _post(client, _thread(client), {"content": "hello"})

    assert "Context from the caller" not in _planner_turn(provider)


@pytest.mark.parametrize(
    "body",
    [
        {"content": "x", "context": {"refs": [HONEY] * 21}},
        {"content": "x", "context": {"facts": ["a fact"] * 6}},
        {"content": "x", "context": {"facts": ["x" * 201]}},
        {"content": "x", "context": {"refs": [{**HONEY, "colour": "red"}]}},
        {"content": "x", "context": {"notes": []}},
        {"content": "x", "mode": "write"},
    ],
)
def test_context_and_mode_are_bounded_and_strict(
    client: TestClient, body: dict[str, Any]
) -> None:
    _use_loop(ScriptedProvider([]))

    assert _post(client, _thread(client), body).status_code == 422


# --- read mode -------------------------------------------------------------------


def test_read_mode_never_runs_a_write_and_goes_on_from_its_reads(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    provider = ScriptedProvider(
        [
            tool_calls_turn(("create_task", {"title": "Sneaky"}), ("list_tasks", {})),
            text_turn("Nothing is due."),
        ]
    )
    _use_loop(provider)

    response = _post(
        client, _thread(client), {"content": "what's due?", "mode": "read"}
    )

    assert response.status_code == 200
    record = response.json()["assistant_message"]["record"]
    assert [(e["tool"], e["outcome"]) for e in record] == [
        ("create_task", "not_run"),
        ("list_tasks", "looked_up"),
    ]
    assert record[0]["reason"] == "not run: read mode"
    assert db_session.execute(select(Task)).scalars().all() == []
    # The model was told, in the tool's own answer.
    told = [m for m in provider.requests[1]["messages"] if m.get("role") == "tool"]
    assert "not run: read mode" in str(told[0]["content"])
    assert db_session.execute(
        select(ConversationMessage.mode).where(ConversationMessage.role == ConversationRole.user)
    ).scalar_one() == "read"


def test_a_read_mode_run_asking_again_for_its_write_ends_answered(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    """The eval's commonest path (#527): read, write refused, the same write
    again. That repeat is no stall: the run ends ``completed`` from its reads."""
    write = ("create_task", {"title": "Sneaky"})
    provider = ScriptedProvider(
        [tool_calls_turn(("list_tasks", {})), tool_calls_turn(write), tool_calls_turn(write)],
        narrations=[text_turn("Nothing is due, and I changed nothing.")],
    )
    _use_loop(provider)

    response = _post(client, _thread(client), {"content": "what's due?", "mode": "read"})

    reply = response.json()["assistant_message"]
    assert reply["stop_reason"] == "completed"
    assert [e["outcome"] for e in reply["record"]] == ["looked_up", "not_run", "not_run"]
    assert db_session.execute(select(Task)).scalars().all() == []


def test_a_confirm_token_on_a_read_mode_message_is_422(client: TestClient) -> None:
    _use_loop(ScriptedProvider([]))

    response = _post(
        client,
        _thread(client),
        {"content": "yes", "mode": "read"},
        headers={"Confirm-Token": "anything", "X-Agent-Actor": "agent:conductor"},
    )

    assert response.status_code == 422


# --- Idempotency-Key: the same message is words, context and mode -------------------


@pytest.mark.parametrize(
    "retry",
    [
        {"content": "hello", "context": {"refs": [HONEY]}, "mode": "read"},
        {"content": "hello", "mode": "read"},
        {"content": "hello", "context": {"refs": [HONEY], "facts": ["more"]}},
    ],
)
def test_a_key_retried_with_other_context_or_mode_is_422(
    client: TestClient, retry: dict[str, Any]
) -> None:
    _use_loop(ScriptedProvider([text_turn("Hi.")]))
    conversation_id = _thread(client)
    first = {"content": "hello", "context": {"refs": [HONEY]}}
    headers = {"Idempotency-Key": "k-1"}

    assert _post(client, conversation_id, first, headers).status_code == 200
    assert _post(client, conversation_id, retry, headers).status_code == 422
    replay = _post(client, conversation_id, first, headers)
    assert replay.status_code == 200


# --- needs_input -----------------------------------------------------------------


def test_an_ask_that_did_nothing_says_needs_input_with_its_candidates(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    book, pay = _seed_dentists(tool_db)
    _use_loop(ScriptedProvider([tool_calls_turn(("ask_user", {"task_ids": [pay, book]}))]))
    conversation_id = _thread(client)

    response = _post(client, conversation_id, {"content": "mark the dentist one done"})

    asked = response.json()["assistant_message"]["needs_input"]
    assert asked == {
        "question": delegate_context.QUESTION,
        "candidates": [
            {"app": "tasks", "kind": "task", "id": str(pay), "title": "Pay the dentist bill"},
            {"app": "tasks", "kind": "task", "id": str(book), "title": "Book dentist appointment"},
        ],
    }
    assert response.json()["user_message"]["needs_input"] is None
    history = client.get(f"/api/agent/conversations/{conversation_id}").json()
    assert history["messages"][1]["needs_input"] == asked


def test_a_turn_that_asks_nothing_has_no_needs_input(client: TestClient) -> None:
    _use_loop(ScriptedProvider([text_turn("Hi.")]))

    response = _post(client, _thread(client), {"content": "hello"})

    assert response.json()["assistant_message"]["needs_input"] is None


def test_a_run_that_wrote_before_it_asked_is_not_needs_input() -> None:
    ask = {
        "tool": "ask_user",
        "arguments": {"task_ids": [1, 2]},
        "result": json.dumps([{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]),
    }
    wrote = {
        "tool": "create_task",
        "arguments": {"title": "C"},
        "result": json.dumps({"id": 3, "title": "C"}),
    }

    assert delegate_context.needs_input([ask]) is not None
    assert delegate_context.needs_input([wrote, ask]) is None
    assert delegate_context.needs_input([{**ask, "result": None, "error": "nope"}]) is None


def test_a_picked_ref_is_accepted_and_marked_for_the_planner(client: TestClient) -> None:
    """conductor#196: the person's answer to an ``ask_user`` comes back as the
    candidate it picked, ``picked: true``."""
    provider = ScriptedProvider([text_turn("ok")])
    _use_loop(provider)
    ref = {"app": "tasks", "kind": "task", "id": "7", "title": "Pay the bill", "picked": True}

    response = _post(client, _thread(client), {"content": "the second one", "context": {"refs": [ref]}})

    assert response.status_code == 200
    assert "- task 7 “Pay the bill”, the person's pick from your question" in _planner_turn(provider)
