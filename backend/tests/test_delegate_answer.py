"""Delegate API v1.3 (conductor#241): ``answer``, a read's reply for the person.

The contract is ``../agent-standard/delegate-api.md`` "v1.3"; these pin
PCC's side of it at the route, over a scripted provider.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import delegate_context, narration, recall
from app.ai.loop import AgentLoop
from app.api import routes_agent
from app.main import app
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.tools import runtime
from tests.scripted_provider import ScriptedProvider, text_turn, tool_calls_turn

CONDUCTOR = {"X-Agent-Actor": "agent:conductor"}
REPORT = (
    "You have 2 open tasks in Home. Nothing is blocked.\n\n"
    "**Home**\n- Book dentist appointment\n- Pay the dentist bill"
)


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    with factory() as db:
        home = projects_service.create_project(db, name="Home")
        tasks_service.create_task(db, project_id=home.id, title="Book dentist appointment")
        tasks_service.create_task(db, project_id=home.id, title="Pay the dentist bill")
        db.commit()
    yield factory


def _use_loop(provider: ScriptedProvider) -> None:
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)


def _post(
    client: TestClient, body: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    thread = client.post("/api/agent/conversations", json={}).json()["id"]
    return client.post(
        f"/api/agent/conversations/{thread}/messages", json=body, headers=headers or {}
    )


def test_a_delegate_read_carries_the_narrators_answer_beside_the_handoff(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    provider = ScriptedProvider(
        [tool_calls_turn(("list_tasks", {"exclude_done": True})), text_turn("Two open tasks.")],
        narrations=[text_turn(REPORT)],
    )
    _use_loop(provider)

    response = _post(client, {"content": "give me a report of my tasks", "mode": "read"}, CONDUCTOR)

    assert response.status_code == 200
    assistant = response.json()["assistant_message"]
    # The handoff is unchanged: a v1.2 caller reads on as before.
    assert assistant["content"].startswith("PCC's record of this run")
    assert [entry["outcome"] for entry in assistant["record"]] == ["looked_up"]
    assert assistant["answer"] == {
        "text": REPORT,
        "spoken": "You have 2 open tasks in Home. Nothing is blocked.",
        "untrusted": False,
    }
    # The narrator read the record with what the look-up answered, and was
    # told its opening may be all that is heard.
    brief = provider.requests[-1]["messages"][1]["content"]
    assert "Book dentist appointment" in brief
    assert brief.endswith(narration.RELAYED_CLOSE)
    assert not provider.requests[-1]["tools"]


def test_a_retried_key_replays_the_stored_answer(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    provider = ScriptedProvider(
        [tool_calls_turn(("list_tasks", {})), text_turn("Two.")], narrations=[text_turn(REPORT)]
    )
    _use_loop(provider)
    thread = client.post("/api/agent/conversations", json={}).json()["id"]
    url = f"/api/agent/conversations/{thread}/messages"
    headers = {**CONDUCTOR, "Idempotency-Key": "k1"}

    first = client.post(url, json={"content": "what's open?"}, headers=headers).json()
    again = client.post(url, json={"content": "what's open?"}, headers=headers).json()

    assert again["assistant_message"]["answer"] == first["assistant_message"]["answer"]
    assert first["assistant_message"]["answer"]["text"] == REPORT
    assert len(provider.requests) == 3


def test_a_run_that_wrote_gets_no_answer_and_no_narrator_call(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    provider = ScriptedProvider(
        [
            tool_calls_turn(("create_task", {"title": "Alpha"})),
            tool_calls_turn(("list_tasks", {})),
            text_turn("Made Alpha; three tasks now."),
        ]
    )
    _use_loop(provider)

    assistant = _post(client, {"content": "add Alpha, then list them"}, CONDUCTOR).json()[
        "assistant_message"
    ]

    assert assistant["answer"] is None
    assert len(provider.requests) == 3


def test_a_refused_write_in_read_mode_gets_no_answer(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    provider = ScriptedProvider(
        [
            tool_calls_turn(("list_tasks", {}), ("create_task", {"title": "Alpha"})),
            text_turn("Couldn't add it."),
        ]
    )
    _use_loop(provider)

    assistant = _post(client, {"content": "add Alpha", "mode": "read"}, CONDUCTOR).json()[
        "assistant_message"
    ]

    assert [entry["outcome"] for entry in assistant["record"]] == ["looked_up", "not_run"]
    assert assistant["answer"] is None


def test_a_run_with_no_lookup_gets_no_answer(client: TestClient) -> None:
    _use_loop(ScriptedProvider([text_turn("Nothing to look up.")]))
    assistant = _post(client, {"content": "hello"}, CONDUCTOR).json()["assistant_message"]
    assert assistant["record"] == [] and assistant["answer"] is None


def _planner_calls(provider: ScriptedProvider) -> list[dict[str, Any]]:
    return [request for request in provider.requests if request["tools"]]


def test_a_read_asked_again_looks_again_and_carries_the_answer(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    """#558, live: the same read in a thread that already held its answer
    called nothing and rewrote the old report, so no ``answer``. Sent back
    once, it looks."""
    provider = ScriptedProvider(
        [
            tool_calls_turn(("list_tasks", {"exclude_done": True})),
            text_turn("Two open tasks."),
            text_turn("Two open tasks, as before."),
            tool_calls_turn(("list_tasks", {"exclude_done": True})),
            text_turn("Two open tasks."),
        ],
        narrations=[text_turn(REPORT), text_turn(REPORT)],
    )
    _use_loop(provider)
    thread = client.post("/api/agent/conversations", json={}).json()["id"]
    url = f"/api/agent/conversations/{thread}/messages"
    ask = {"content": "give me a report of my tasks", "mode": "read"}

    client.post(url, json=ask, headers=CONDUCTOR)
    again = client.post(url, json=ask, headers=CONDUCTOR).json()["assistant_message"]

    assert again["stop_reason"] == "completed"
    assert [(e["tool"], e["outcome"]) for e in again["record"]] == [("list_tasks", "looked_up")]
    assert again["answer"]["text"] == REPORT
    first, sent_back = _planner_calls(provider)[2:4]
    # The history it read is labelled as of then.
    assert recall.AS_OF_THEN_HEAD in first["messages"][1]["content"]
    # Its words stay in the transcript, and the correction follows them.
    assert sent_back["messages"][-2] == {
        "role": "assistant",
        "content": "Two open tasks, as before.",
    }
    assert sent_back["messages"][-1] == {"role": "user", "content": delegate_context.LOOK_AGAIN}


def test_a_read_that_still_calls_nothing_finishes_as_before(client: TestClient) -> None:
    provider = ScriptedProvider([text_turn("Hello."), text_turn("Nothing to look up.")])
    _use_loop(provider)

    assistant = _post(client, {"content": "hello", "mode": "read"}, CONDUCTOR).json()[
        "assistant_message"
    ]

    assert assistant["stop_reason"] == "completed"
    assert assistant["record"] == [] and assistant["answer"] is None
    assert "Nothing to look up." in assistant["content"]
    assert len(provider.requests) == 2


def test_a_read_with_no_corrections_left_is_not_sent_back(
    tool_db: sessionmaker[Session],
) -> None:
    provider = ScriptedProvider([text_turn("Hello.")])

    result = AgentLoop(provider, max_corrections=0).run(
        "hello", actor="agent:conductor", read_mode=True
    )

    assert result.stop_reason == "completed" and len(provider.requests) == 1


@pytest.mark.parametrize("headers", [CONDUCTOR, {}], ids=["delegate", "in_app"])
def test_a_run_not_in_read_mode_is_not_sent_back(
    client: TestClient, headers: dict[str, str]
) -> None:
    """In-app chat and a delegate's write mode keep answering follow-ups from
    the thread ("which of those is oldest?"), unlabelled."""
    provider = ScriptedProvider([text_turn("Hi."), text_turn("Still here.")])
    _use_loop(provider)
    thread = client.post("/api/agent/conversations", json={}).json()["id"]
    url = f"/api/agent/conversations/{thread}/messages"

    client.post(url, json={"content": "hello"}, headers=headers)
    client.post(url, json={"content": "still there?"}, headers=headers)

    planner = _planner_calls(provider)
    assert len(planner) == 2
    follow_up = planner[1]["messages"]
    assert [m["role"] for m in follow_up] == ["system", "user"]
    assert f"\n\n{recall.HEAD}\n\n" in follow_up[1]["content"]
    assert recall.AS_OF_THEN_HEAD not in follow_up[1]["content"]


def test_a_narrator_that_cannot_speak_leaves_the_handoff_alone(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    provider = ScriptedProvider(
        [tool_calls_turn(("list_tasks", {})), text_turn("Two.")],
        narrations=[RuntimeError("provider down")],
    )
    _use_loop(provider)

    response = _post(client, {"content": "what's open?"}, CONDUCTOR)

    assert response.status_code == 200
    assistant = response.json()["assistant_message"]
    assert assistant["answer"] is None
    assert assistant["content"].startswith("PCC's record of this run")


def test_a_human_callers_read_has_no_answer_field_set(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    provider = ScriptedProvider(
        [tool_calls_turn(("list_tasks", {})), text_turn("Two.")], narrations=[text_turn(REPORT)]
    )
    _use_loop(provider)
    assistant = _post(client, {"content": "what's open?"}).json()["assistant_message"]
    assert assistant["content"] == REPORT and assistant["answer"] is None


@pytest.mark.parametrize(
    ("reply", "spoken"),
    [
        ("One line.", "One line."),
        ("A. B. C. D.\n\nMore.", "A. B."),
        ("**3 due** this week.\n\n- one\n- two", "3 due this week."),
        ("- one\n- two", None),
        ("Here's the list:\n- one\n- two", "Here's the list:"),
    ],
)
def test_spoken_is_the_opening_cut_to_two_sentences(reply: str, spoken: str | None) -> None:
    answer = narration.answer(reply)
    assert answer is not None
    assert answer["text"] == reply and answer["spoken"] == spoken


def test_an_answer_is_bounded() -> None:
    answer = narration.answer("word " * 2000)
    assert answer is not None
    assert len(answer["text"]) == narration.ANSWER_TEXT_CHARS
    assert len(answer["spoken"]) <= narration.ANSWER_SPOKEN_CHARS
    assert narration.answer("   ") is None and narration.answer(None) is None
