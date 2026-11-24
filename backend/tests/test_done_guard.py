"""Done tasks stay out of a delegate's group delete (#550, ``app/ai/done_guard.py``)."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import confirmation, done_guard
from app.ai.loop import AgentLoop
from app.api import routes_agent
from app.main import app
from app.tools import runtime
from tests.scripted_provider import ScriptedProvider, text_turn, tool_calls_turn

DELEGATE = {"X-Agent-Actor": "agent:conductor"}
OPEN = ("Test the fence", "Test the mower")
DONE = ("Test the docker container", "Test the tools")


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    confirmation.reset()
    yield factory
    confirmation.reset()


def _tasks(client: TestClient) -> dict[str, int]:
    """Two open and two done tasks; ids by title."""
    project_id = int(client.post("/api/projects", json={"name": "Tests"}).json()["id"])
    ids: dict[str, int] = {}
    for title in (*OPEN, *DONE):
        response = client.post("/api/tasks", json={"title": title, "project_id": project_id})
        assert response.status_code == 201, response.text
        ids[title] = int(response.json()["id"])
    for title in DONE:
        response = client.patch(f"/api/tasks/{ids[title]}", json={"workflow_status": "done"})
        assert response.status_code == 200, response.text
    return ids


def _ask(
    client: TestClient,
    words: str,
    *turns: Any,
    headers: dict[str, str] | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    provider = ScriptedProvider(list(turns))
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)
    if conversation_id is None:
        conversation_id = int(client.post("/api/agent/conversations", json={}).json()["id"])
    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": words},
        headers=DELEGATE if headers is None else headers,
    )
    assert response.status_code == 200, response.text
    message: dict[str, Any] = response.json()["assistant_message"]
    return {**message, "conversation_id": conversation_id}


def _trash(*task_ids: int) -> Any:
    return tool_calls_turn(*[("trash_task", {"task_id": i}) for i in task_ids])


def _trashed(client: TestClient, task_id: int) -> bool:
    return client.get(f"/api/tasks/{task_id}").status_code == 404


def test_a_group_delete_asks_about_the_open_tasks_only(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _tasks(client)
    order = [ids[OPEN[0]], ids[DONE[0]], ids[OPEN[1]], ids[DONE[1]]]

    asked = _ask(
        client,
        "Delete the test tasks. (the person's answer to your question: Delete the test tasks.)",
        _trash(*order),
    )

    assert asked["stop_reason"] == "confirm_required"
    assert [c["question"] for c in asked["confirms_required"]] == [
        f'Trash task "{title}"?' for title in OPEN
    ]
    assert [entry["reason"] for entry in asked["record"]] == [
        confirmation.NOT_RUN_REASON,
        done_guard.NOT_RUN_ERROR,
        confirmation.NOT_RUN_REASON,
        done_guard.NOT_RUN_ERROR,
    ]
    assert not any(_trashed(client, i) for i in order)


def test_a_done_task_leading_the_turn_is_still_held_back(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _tasks(client)

    asked = _ask(client, "delete the test tasks", _trash(ids[DONE[0]], ids[OPEN[0]]))

    assert [c["question"] for c in asked["confirms_required"]] == [f'Trash task "{OPEN[0]}"?']


@pytest.mark.parametrize(
    "words",
    ["delete the done test tasks", "Delete the finished ones too.", "delete all completed tasks"],
)
def test_words_that_ask_for_done_tasks_keep_them(
    client: TestClient, tool_db: sessionmaker[Session], words: str
) -> None:
    ids = _tasks(client)

    asked = _ask(client, words, _trash(*ids.values()))

    assert len(asked["confirms_required"]) == 4


def test_one_named_done_task_is_asked_about(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _tasks(client)

    asked = _ask(client, "delete the task Test the docker container", _trash(ids[DONE[0]]))

    assert [c["question"] for c in asked["confirms_required"]] == [f'Trash task "{DONE[0]}"?']


def test_a_turn_of_only_done_tasks_runs_none_and_the_run_goes_on(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _tasks(client)
    done = [ids[title] for title in DONE]

    reply = _ask(
        client, "delete the test tasks", _trash(*done), _trash(*done), text_turn("None open.")
    )

    # Told once, then the repeat is no progress: nothing asked, nothing trashed.
    assert reply["stop_reason"] != "confirm_required"
    assert "confirms_required" not in reply or not reply["confirms_required"]
    assert {entry["reason"] for entry in reply["record"]} == {done_guard.NOT_RUN_ERROR}
    assert not any(_trashed(client, i) for i in done)


def test_the_apps_own_yes_still_runs_done_tasks_it_asked_about(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _tasks(client)
    done = [ids[title] for title in DONE]
    asked = _ask(client, "delete the finished test tasks", _trash(*done))
    assert len(asked["confirms_required"]) == 2

    _ask(client, "yes", _trash(*done), text_turn("Done."), conversation_id=asked["conversation_id"])

    assert all(_trashed(client, i) for i in done)


def test_pccs_own_chat_is_not_guarded(client: TestClient, tool_db: sessionmaker[Session]) -> None:
    ids = _tasks(client)

    _ask(client, "delete the test tasks", _trash(*ids.values()), text_turn("Done."), headers={})

    assert all(_trashed(client, i) for i in ids.values())


def test_an_open_questions_request_counts_as_the_persons_words() -> None:
    assert done_guard.asks_for_done("all of them", "delete the done test tasks")
    assert not done_guard.asks_for_done("Delete the test tasks.", None)
    assert not done_guard.asks_for_done("delete the abandoned tasks")
