"""Delegate API v1.1 (#502): the typed record, failure bodies, Idempotency-Key.

The contract is ``../agent-standard/delegate-api.md``; these pin PCC's side of
it at the route, over a scripted provider.
"""

from __future__ import annotations

import json
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import receipts
from app.ai.loop import AgentLoop
from app.ai.providers.llamacpp import ProviderRequestError
from app.api import routes_agent
from app.db.models import Task
from app.main import app
from app.services import conversations as conversations_service
from app.tools import registry, runtime
from tests.scripted_provider import ScriptedProvider, text_turn, tool_calls_turn


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
    client: TestClient, conversation_id: int, content: str, key: str | None = None
) -> httpx.Response:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=headers,
    )


def _create_then_reply(title: str) -> ScriptedProvider:
    return ScriptedProvider(
        [
            tool_calls_turn(("create_task", {"title": title})),
            text_turn(f"Created '{title}'."),
            text_turn(f"You asked me to create '{title}'."),
        ]
    )


# --- The record on a 200 -------------------------------------------------------


def test_reply_carries_a_typed_record_of_each_call(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    _use_loop(_create_then_reply("Book dentist"))
    conversation_id = _thread(client)

    response = _post(client, conversation_id, "Create a task called Book dentist")

    assert response.status_code == 200
    exchange = response.json()
    assert exchange["user_message"]["record"] is None
    [entry] = exchange["assistant_message"]["record"]
    task_id = (
        exchange["assistant_message"]["tool_calls"][0]["arguments"].get("id")
        or json.loads(exchange["assistant_message"]["tool_calls"][0]["result"])["id"]
    )
    assert entry == {
        "tool": "create_task",
        "outcome": "done",
        "touched": [{"app": "tasks", "kind": "task", "id": str(task_id), "title": "Book dentist"}],
        "undo": {"handle": f"trash_task:{task_id}", "how": "move it to the trash"},
    }

    # History reads carry the same record.
    history = client.get(f"/api/agent/conversations/{conversation_id}").json()
    assert history["messages"][1]["record"] == [entry]


def test_a_turn_with_no_tools_has_an_empty_record(client: TestClient) -> None:
    _use_loop(ScriptedProvider([text_turn("Hi."), text_turn("Hi.")]))
    conversation_id = _thread(client)

    response = _post(client, conversation_id, "hello")

    assert response.json()["assistant_message"]["record"] == []


# --- Failure bodies --------------------------------------------------------------


def test_a_5xx_after_a_write_carries_the_partial_record(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    _use_loop(
        ScriptedProvider(
            [
                tool_calls_turn(("create_task", {"title": "Half-done"})),
                ProviderRequestError("llama-server went away"),
            ]
        )
    )
    conversation_id = _thread(client)

    response = _post(client, conversation_id, "Create a task called Half-done")

    assert response.status_code == 502
    body = response.json()
    assert body["detail"]  # v1 clients still get FastAPI's detail
    assert body["stop_reason"] == "provider_error"
    [entry] = body["record"]
    assert entry["outcome"] == "done"
    assert entry["touched"][0]["title"] == "Half-done"
    stored = client.get(f"/api/agent/conversations/{conversation_id}").json()["messages"][1]
    assert body["assistant_message_id"] == stored["id"]
    assert stored["record"] == body["record"]
    assert [t.title for t in db_session.execute(select(Task)).scalars()] == ["Half-done"]


def test_a_5xx_before_any_tool_has_an_empty_record(client: TestClient) -> None:
    _use_loop(ScriptedProvider([ProviderRequestError("down")]))
    conversation_id = _thread(client)

    response = _post(client, conversation_id, "anything")

    assert response.status_code == 502
    assert response.json()["record"] == []


# --- Idempotency-Key ---------------------------------------------------------------


def test_a_retried_key_replays_the_exchange_without_running_again(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    # Script exactly one run: a second run would exhaust the provider.
    _use_loop(_create_then_reply("Once"))
    conversation_id = _thread(client)

    first = _post(client, conversation_id, "Create a task called Once", key="k-1")
    again = _post(client, conversation_id, "Create a task called Once", key="k-1")

    assert again.status_code == 200
    assert again.json() == first.json()
    assert [t.title for t in db_session.execute(select(Task)).scalars()] == ["Once"]
    messages = client.get(f"/api/agent/conversations/{conversation_id}").json()["messages"]
    assert len(messages) == 2


def test_a_retried_key_replays_a_stored_failure(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    _use_loop(
        ScriptedProvider(
            [
                tool_calls_turn(("create_task", {"title": "Half-done"})),
                ProviderRequestError("llama-server went away"),
            ]
        )
    )
    conversation_id = _thread(client)

    first = _post(client, conversation_id, "Create Half-done", key="k-2")
    again = _post(client, conversation_id, "Create Half-done", key="k-2")

    assert again.status_code == first.status_code == 502
    for field in ("stop_reason", "assistant_message_id", "record"):
        assert again.json()[field] == first.json()[field]
    assert [t.title for t in db_session.execute(select(Task)).scalars()] == ["Half-done"]


def test_a_key_reused_for_different_words_is_422(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    _use_loop(_create_then_reply("First"))
    conversation_id = _thread(client)
    _post(client, conversation_id, "Create a task called First", key="k-3")

    response = _post(client, conversation_id, "Create a task called Second", key="k-3")

    assert response.status_code == 422


def test_a_key_whose_exchange_never_finished_is_409(
    client: TestClient, db_session: Session
) -> None:
    _use_loop(ScriptedProvider([]))  # must never be called
    conversation_id = _thread(client)
    conversation = conversations_service.get_conversation(db_session, conversation_id)
    assert conversation is not None
    # The process died mid-run: the user turn is stored, no answer is.
    conversations_service.append_user_message(db_session, conversation, "do it", "k-4")
    db_session.commit()

    response = _post(client, conversation_id, "do it", key="k-4")

    assert response.status_code == 409


def test_a_key_is_scoped_to_its_thread(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    _use_loop(_create_then_reply("A"))
    first_thread = _thread(client)
    _post(client, first_thread, "Create a task called A", key="k-5")

    _use_loop(_create_then_reply("A"))
    other_thread = _thread(client)
    response = _post(client, other_thread, "Create a task called A", key="k-5")

    assert response.status_code == 200
    assert len(db_session.execute(select(Task)).scalars().all()) == 2


def test_an_overlong_key_is_rejected(client: TestClient) -> None:
    _use_loop(ScriptedProvider([]))
    conversation_id = _thread(client)

    response = _post(client, conversation_id, "hi", key="k" * 129)

    assert response.status_code == 422


# --- The record builder, per outcome ------------------------------------------------


def test_record_outcomes_refused_and_not_run_carry_a_reason() -> None:
    refused, not_run = receipts.dump(
        receipts.build(
            [
                {
                    "tool": "complete_task",
                    "arguments": {"task_id": 41},
                    "result": None,
                    "error": "task 41 is blocked by task 39",
                },
                {
                    "tool": "create_task",
                    "arguments": {},
                    "result": None,
                    "error": "not run: the agent run's time budget was exhausted",
                },
            ]
        )
    )
    assert refused == {
        "tool": "complete_task",
        "outcome": "refused",
        "reason": "task 41 is blocked by task 39",
    }
    assert not_run["outcome"] == "not_run" and "time budget" in not_run["reason"]


def test_a_read_lists_what_it_found_and_counts_the_rest() -> None:
    page = {
        "items": [{"id": i, "title": f"T{i}", "workflow_status": "todo"} for i in range(25)],
        "total": 31,
        "has_more": True,
    }
    [entry] = receipts.dump(
        receipts.build([{"tool": "list_tasks", "arguments": {}, "result": json.dumps(page)}])
    )
    assert entry["outcome"] == "looked_up"
    assert len(entry["touched"]) == receipts.MAX_TOUCHED
    assert entry["count"] == 31
    assert "undo" not in entry


def test_a_sentence_answer_names_its_row_from_the_arguments() -> None:
    [entry] = receipts.dump(
        receipts.build(
            [
                {
                    "tool": "trash_project",
                    "arguments": {"project_id": 7},
                    "result": 'Project 7 "Kitchen" and its tasks moved to trash',
                }
            ]
        )
    )
    assert entry["touched"] == [{"app": "tasks", "kind": "project", "id": "7", "title": "Kitchen"}]
    assert entry["undo"]["handle"] == "restore_project:7"


def test_search_matches_keep_their_own_kind() -> None:
    found = {
        "projects": [{"kind": "project", "id": 7, "title": "Kitchen"}],
        "tasks": [{"kind": "task", "id": 3, "title": "Mop", "workflow_status": "todo"}],
    }
    [entry] = receipts.dump(
        receipts.build(
            [{"tool": "search", "arguments": {"query": "k"}, "result": json.dumps(found)}]
        )
    )
    assert [(r["kind"], r["id"]) for r in entry["touched"]] == [("project", "7"), ("task", "3")]


def test_every_write_has_an_undo_rule() -> None:
    """A new write tool must say how it's undone, or that it can't be."""
    reads = registry.read_only_tool_names()
    writes = {tool.name for tool in registry.all_tools()} - reads - {"ask_user"}
    assert writes - set(receipts._UNDO) == set()
