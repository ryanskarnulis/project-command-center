"""Structural confirmation over the delegate API (#503).

The contract is ``../agent-standard/delegate-api.md`` "Confirmation": a
delegate's destructive call never runs unasked; a ``Confirm-Token`` runs
exactly that call once; the token is bound to the thread, the call and the
state it previewed. PCC's own chat is never gated.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import confirmation
from app.ai.loop import AgentLoop
from app.api import routes_agent
from app.db.models import Project
from app.main import app
from app.tools import runtime
from tests.scripted_provider import ScriptedProvider, text_turn, tool_calls_turn

DELEGATE = {"X-Agent-Actor": "agent:conductor"}


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


def _use_loop(provider: ScriptedProvider) -> None:
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)


def _thread(client: TestClient) -> int:
    return int(client.post("/api/agent/conversations", json={}).json()["id"])


def _post(
    client: TestClient,
    conversation_id: int,
    content: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    response: httpx.Response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": content},
        headers={**DELEGATE, **(headers or {})},
    )
    return response


def _kitchen(client: TestClient) -> int:
    """A project with two tasks."""
    project_id = int(client.post("/api/projects", json={"name": "Kitchen"}).json()["id"])
    for title in ("Mop", "Descale kettle"):
        _add_task(client, project_id, title)
    return project_id


def _add_task(client: TestClient, project_id: int, title: str) -> None:
    response = client.post("/api/tasks", json={"title": title, "project_id": project_id})
    assert response.status_code == 201, response.text


def _alive(db: Session, project_id: int) -> bool:
    db.expire_all()
    project = db.get(Project, project_id)
    return project is not None and project.deleted_at is None


def _ask_to_trash(client: TestClient, project_id: int) -> tuple[int, dict[str, Any]]:
    _use_loop(ScriptedProvider([tool_calls_turn(("trash_project", {"project_id": project_id}))]))
    conversation_id = _thread(client)
    response = _post(client, conversation_id, "trash the Kitchen project")
    assert response.status_code == 200
    return conversation_id, response.json()["assistant_message"]


def test_a_delegate_trash_asks_first_and_runs_nothing(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    project_id = _kitchen(client)

    _, asked = _ask_to_trash(client, project_id)

    assert asked["stop_reason"] == "confirm_required"
    assert asked["content"] == 'Trash project "Kitchen" and its 2 tasks?'
    confirm = asked["confirm_required"]
    assert confirm["tool"] == "trash_project"
    assert confirm["question"] == asked["content"]
    assert confirm["preview"] == {
        "count": 3,
        "touched": [{"app": "tasks", "kind": "project", "id": str(project_id), "title": "Kitchen"}],
    }
    assert confirm["token"] and confirm["expires_at"]
    assert asked["record"] == [
        {"tool": "trash_project", "outcome": "not_run", "reason": confirmation.NOT_RUN_REASON}
    ]
    assert _alive(db_session, project_id)


def test_the_token_runs_exactly_that_call_once_with_no_model_turn(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    project_id = _kitchen(client)
    conversation_id, asked = _ask_to_trash(client, project_id)
    token = asked["confirm_required"]["token"]
    _use_loop(ScriptedProvider([]))  # the model must not be called

    response = _post(client, conversation_id, "yes, trash it", headers={"Confirm-Token": token})

    assert response.status_code == 200
    done = response.json()["assistant_message"]
    assert done["stop_reason"] == "completed"
    [entry] = done["record"]
    assert entry["outcome"] == "done" and entry["tool"] == "trash_project"
    assert entry["undo"]["handle"] == f"restore_project:{project_id}"
    assert not _alive(db_session, project_id)

    again = _post(client, conversation_id, "yes", headers={"Confirm-Token": token})
    assert again.status_code == 409
    assert again.json()["confirm"] == "used"


def test_a_token_from_another_thread_is_unknown(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    project_id = _kitchen(client)
    _, asked = _ask_to_trash(client, project_id)
    _use_loop(ScriptedProvider([]))
    other = _thread(client)

    response = _post(
        client, other, "yes", headers={"Confirm-Token": asked["confirm_required"]["token"]}
    )

    assert response.status_code == 409
    assert response.json()["confirm"] == "unknown"
    assert _alive(db_session, project_id)


def test_an_expired_token_is_refused(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    project_id = _kitchen(client)
    conversation_id, asked = _ask_to_trash(client, project_id)
    token = asked["confirm_required"]["token"]
    confirmation._by_token[token].expires_at = 0.0
    _use_loop(ScriptedProvider([]))

    response = _post(client, conversation_id, "yes", headers={"Confirm-Token": token})

    assert response.status_code == 409
    assert response.json()["confirm"] in ("expired", "unknown")
    assert _alive(db_session, project_id)


def test_a_token_whose_preview_changed_is_stale(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    project_id = _kitchen(client)
    conversation_id, asked = _ask_to_trash(client, project_id)
    # A third task lands after the question: "its 2 tasks" is no longer true.
    _add_task(client, project_id, "Fix tap")
    _use_loop(ScriptedProvider([]))

    response = _post(
        client,
        conversation_id,
        "yes",
        headers={"Confirm-Token": asked["confirm_required"]["token"]},
    )

    assert response.status_code == 409
    assert response.json()["confirm"] == "stale"
    assert _alive(db_session, project_id)


def test_the_apps_own_yes_runs_the_identical_call(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    """The v1 path: no token, but the next run re-issues the same call."""
    project_id = _kitchen(client)
    conversation_id, _ = _ask_to_trash(client, project_id)
    _use_loop(
        ScriptedProvider(
            [
                tool_calls_turn(("trash_project", {"project_id": project_id})),
                text_turn("Trashed it."),
                text_turn("Trashed it."),
            ]
        )
    )

    response = _post(client, conversation_id, "yes")

    assert response.status_code == 200
    body = response.json()["assistant_message"]
    assert body["stop_reason"] == "completed"
    assert body["record"][0]["outcome"] == "done"
    assert not _alive(db_session, project_id)


def test_any_other_message_voids_the_question(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    project_id = _kitchen(client)
    conversation_id, asked = _ask_to_trash(client, project_id)
    _use_loop(ScriptedProvider([text_turn("Okay, leaving it."), text_turn("Okay.")]))
    _post(client, conversation_id, "actually, never mind")
    _use_loop(ScriptedProvider([]))

    response = _post(
        client,
        conversation_id,
        "yes",
        headers={"Confirm-Token": asked["confirm_required"]["token"]},
    )

    assert response.status_code == 409
    assert response.json()["confirm"] == "unknown"
    assert _alive(db_session, project_id)


def test_pccs_own_chat_is_never_gated(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    project_id = _kitchen(client)
    _use_loop(
        ScriptedProvider(
            [
                tool_calls_turn(("trash_project", {"project_id": project_id})),
                text_turn("Trashed."),
                text_turn("Trashed Kitchen."),
            ]
        )
    )
    conversation_id = _thread(client)

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "trash the Kitchen project"},
    )

    assert response.json()["assistant_message"]["stop_reason"] == "completed"
    assert response.json()["assistant_message"]["confirm_required"] is None
    assert not _alive(db_session, project_id)


def test_a_missing_row_arms_nothing_and_is_refused_as_usual(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    _use_loop(
        ScriptedProvider(
            [
                tool_calls_turn(("trash_project", {"project_id": 999})),
                text_turn("There's no such project."),
                text_turn("There's no such project."),
            ]
        )
    )
    conversation_id = _thread(client)

    body = _post(client, conversation_id, "trash project 999").json()["assistant_message"]

    assert body["confirm_required"] is None
    assert body["record"][0]["outcome"] == "refused"


def test_a_replayed_question_still_carries_its_token(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    project_id = _kitchen(client)
    _use_loop(ScriptedProvider([tool_calls_turn(("trash_project", {"project_id": project_id}))]))
    conversation_id = _thread(client)
    key = {"Idempotency-Key": "ask-1"}

    first = _post(client, conversation_id, "trash Kitchen", headers=key)
    again = _post(client, conversation_id, "trash Kitchen", headers=key)

    assert again.json() == first.json()
    assert again.json()["assistant_message"]["confirm_required"]["token"]


# --- several gated calls in one turn (#545) -----------------------------------


def _three_tasks(client: TestClient) -> list[int]:
    project_id = int(client.post("/api/projects", json={"name": "Tests"}).json()["id"])
    ids = []
    for title in ("Test the fence", "Test the mower", "Test the tools"):
        response = client.post("/api/tasks", json={"title": title, "project_id": project_id})
        assert response.status_code == 201, response.text
        ids.append(int(response.json()["id"]))
    return ids


def _trashed(client: TestClient, task_id: int) -> bool:
    return client.get(f"/api/tasks/{task_id}").status_code == 404


def _ask_to_trash_tasks(client: TestClient, ids: list[int]) -> tuple[int, dict[str, Any]]:
    _use_loop(ScriptedProvider([tool_calls_turn(*[("trash_task", {"task_id": i}) for i in ids])]))
    conversation_id = _thread(client)
    response = _post(client, conversation_id, "delete the test tasks")
    assert response.status_code == 200
    return conversation_id, response.json()["assistant_message"]


def _yes(client: TestClient, conversation_id: int, token: str) -> httpx.Response:
    return _post(client, conversation_id, "yes", headers={"Confirm-Token": token})


def test_every_trash_of_the_turn_gets_its_own_question(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _three_tasks(client)

    _, asked = _ask_to_trash_tasks(client, ids)

    assert asked["stop_reason"] == "confirm_required"
    questions = [
        'Trash task "Test the fence"?',
        'Trash task "Test the mower"?',
        'Trash task "Test the tools"?',
    ]
    assert asked["content"] == "\n".join(questions)
    confirms = asked["confirms_required"]
    assert [c["question"] for c in confirms] == questions
    assert [c["preview"]["touched"][0]["id"] for c in confirms] == [str(i) for i in ids]
    assert len({c["token"] for c in confirms}) == 3
    assert asked["confirm_required"] == confirms[0]
    assert [entry["reason"] for entry in asked["record"]] == [confirmation.NOT_RUN_REASON] * 3
    assert not any(_trashed(client, i) for i in ids)


def test_each_token_runs_its_own_call_and_leaves_the_others_open(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _three_tasks(client)
    conversation_id, asked = _ask_to_trash_tasks(client, ids)
    tokens = [c["token"] for c in asked["confirms_required"]]
    _use_loop(ScriptedProvider([]))  # the model must not be called

    # Answered out of order; the middle one is never answered.
    last = _yes(client, conversation_id, tokens[2])
    first = _yes(client, conversation_id, tokens[0])

    for response, task_id in ((last, ids[2]), (first, ids[0])):
        assert response.status_code == 200
        [entry] = response.json()["assistant_message"]["record"]
        assert entry["outcome"] == "done"
        assert entry["undo"]["handle"] == f"restore_task:{task_id}"
    assert [_trashed(client, i) for i in ids] == [True, False, True]
    assert _yes(client, conversation_id, tokens[0]).json()["confirm"] == "used"


def test_one_stale_row_refuses_only_its_own_token(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _three_tasks(client)
    conversation_id, asked = _ask_to_trash_tasks(client, ids)
    tokens = [c["token"] for c in asked["confirms_required"]]
    assert client.patch(f"/api/tasks/{ids[2]}", json={"title": "Keep me"}).status_code == 200

    stale = _yes(client, conversation_id, tokens[2])
    fine = _yes(client, conversation_id, tokens[1])

    assert stale.status_code == 409 and stale.json()["confirm"] == "stale"
    assert fine.status_code == 200
    assert [_trashed(client, i) for i in ids] == [False, True, False]


def test_a_message_without_a_token_voids_every_open_question(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _three_tasks(client)
    conversation_id, asked = _ask_to_trash_tasks(client, ids)
    _use_loop(ScriptedProvider([text_turn("Okay."), text_turn("Okay.")]))

    assert _post(client, conversation_id, "never mind").status_code == 200

    for confirm in asked["confirms_required"]:
        refused = _yes(client, conversation_id, confirm["token"])
        assert refused.status_code == 409 and refused.json()["confirm"] == "unknown"
    assert not any(_trashed(client, i) for i in ids)


def test_the_apps_own_yes_runs_the_calls_asked_about_and_no_others(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _three_tasks(client)
    conversation_id, _ = _ask_to_trash_tasks(client, ids[:2])
    _use_loop(
        ScriptedProvider(
            [tool_calls_turn(*[("trash_task", {"task_id": i}) for i in ids])],
        )
    )

    response = _post(client, conversation_id, "yes")

    body = response.json()["assistant_message"]
    # The two asked about ran; the third was never asked, so it asks now.
    assert [entry["outcome"] for entry in body["record"]] == ["done", "done", "not_run"]
    assert body["stop_reason"] == "confirm_required"
    assert body["content"] == 'Trash task "Test the tools"?'
    assert [_trashed(client, i) for i in ids] == [True, True, False]


def test_a_repeated_call_is_asked_once_and_ungated_calls_are_not_held(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    project_id = _kitchen(client)
    [task_id, *_] = _three_tasks(client)
    _use_loop(
        ScriptedProvider(
            [
                tool_calls_turn(
                    ("trash_task", {"task_id": task_id}),
                    ("trash_project", {"project_id": project_id}),
                    ("trash_task", {"task_id": task_id}),
                    ("list_tasks", {}),
                )
            ]
        )
    )

    asked = _post(client, _thread(client), "clear these out").json()["assistant_message"]

    assert [c["question"] for c in asked["confirms_required"]] == [
        'Trash task "Test the fence"?',
        'Trash project "Kitchen" and its 2 tasks?',
    ]
    assert [c["preview"]["count"] for c in asked["confirms_required"]] == [1, 3]
    assert [entry["outcome"] for entry in asked["record"]] == ["not_run"] * 4


def test_a_replayed_question_still_carries_every_open_token(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    ids = _three_tasks(client)
    _use_loop(ScriptedProvider([tool_calls_turn(*[("trash_task", {"task_id": i}) for i in ids])]))
    conversation_id = _thread(client)
    key = {"Idempotency-Key": "delete-tests"}
    first = _post(client, conversation_id, "delete the test tasks", headers=key).json()
    tokens = [c["token"] for c in first["assistant_message"]["confirms_required"]]
    assert _yes(client, conversation_id, tokens[0]).status_code == 200

    replay = _post(client, conversation_id, "delete the test tasks", headers=key).json()

    assert [c["token"] for c in replay["assistant_message"]["confirms_required"]] == tokens[1:]
