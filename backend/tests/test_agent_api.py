"""Conversation persistence + agent API (loop epic, slice 2).

Service happy path against the in-memory DB, then the routes end-to-end with
the real ``AgentLoop`` over a :class:`ScriptedProvider` — the full
user-message → loop → tool dispatch → persisted-exchange path, no GPU.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.ai.loop import LOOP_ACTOR, AgentLoop, AgentRunResult, ToolCallRecord
from app.ai.providers.llamacpp import ProviderRequestError
from app.api import conversation_locks, routes_agent
from app.config import get_settings
from app.db.models import ActivityEvent, Task
from app.main import app
from app.services import conversations as conversations_service
from app.tools import registry, runtime
from tests.scripted_provider import ScriptedProvider, text_turn, tool_calls_turn


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    """Point the loop's per-tool-call sessions at the test engine."""
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def _use_loop(provider: ScriptedProvider) -> None:
    """Route the agent endpoints at a scripted loop for this test."""
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)


# --- Service ------------------------------------------------------------------


def test_conversation_service_happy_path(db_session: Session) -> None:
    conversation = conversations_service.create_conversation(db_session)
    assert conversation.title is None

    conversations_service.append_user_message(
        db_session, conversation, "Plan my week around the garage cleanup"
    )
    # Untitled conversations take their title from the first user message.
    assert conversation.title == "Plan my week around the garage cleanup"

    run = AgentRunResult(
        reply="Here is the plan.",
        stop_reason="completed",
        tool_calls=[
            ToolCallRecord(
                tool="get_focus_plan", arguments={}, result='{"blocks": []}'
            )
        ],
        iterations=2,
        messages=[],
    )
    conversations_service.append_assistant_message(db_session, conversation, run)

    messages = conversations_service.list_messages(db_session, conversation.id)
    assert [m.role.value for m in messages] == ["user", "assistant"]
    assert messages[1].content == "Here is the plan."
    assert messages[1].stop_reason == "completed"
    assert messages[1].tool_calls is not None
    assert messages[1].tool_calls[0]["tool"] == "get_focus_plan"

    # History for the next run: text turns only, no tool transcripts.
    history = conversations_service.history_for_loop(db_session, conversation.id)
    assert history == [
        {"role": "user", "content": "Plan my week around the garage cleanup"},
        {"role": "assistant", "content": "Here is the plan."},
    ]

    conversations_service.soft_delete_conversation(db_session, conversation)
    assert conversations_service.list_conversations(db_session) == []
    assert conversations_service.get_conversation(db_session, conversation.id) is None


def test_derived_title_cuts_on_word_boundary(db_session: Session) -> None:
    conversation = conversations_service.create_conversation(db_session)
    long_ask = (
        "Please go through every project I have and reschedule anything that "
        "is overdue to sometime next week"
    )
    conversations_service.append_user_message(db_session, conversation, long_ask)
    assert conversation.title is not None
    assert len(conversation.title) <= 61  # 60 + ellipsis
    assert conversation.title.endswith("…")
    assert not conversation.title[:-1].endswith(" ")


# --- Routes -------------------------------------------------------------------


def test_conversation_crud_over_api(client: TestClient) -> None:
    created = client.post("/api/agent/conversations", json={"title": "Weekly triage"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert created.json()["title"] == "Weekly triage"

    listed = client.get("/api/agent/conversations")
    assert [c["id"] for c in listed.json()] == [conversation_id]

    detail = client.get(f"/api/agent/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert detail.json()["messages"] == []

    assert (
        client.delete(f"/api/agent/conversations/{conversation_id}").status_code == 204
    )
    assert client.get(f"/api/agent/conversations/{conversation_id}").status_code == 404
    assert (
        client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            json={"content": "hello?"},
        ).status_code
        == 404
    )


def test_post_message_runs_loop_and_persists_exchange(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    provider = ScriptedProvider(
        [
            tool_calls_turn(("create_task", {"data": {"title": "Ship slice 2"}})),
            text_turn("Created 'Ship slice 2' in General."),
            text_turn("You asked me to create the task 'Ship slice 2'."),
        ]
    )
    _use_loop(provider)

    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]
    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Create a task called Ship slice 2"},
    )
    assert response.status_code == 200
    exchange = response.json()
    assert exchange["user_message"]["role"] == "user"
    assert exchange["assistant_message"]["content"] == (
        "Created 'Ship slice 2' in General."
    )
    assert exchange["assistant_message"]["stop_reason"] == "completed"
    tool_calls = exchange["assistant_message"]["tool_calls"]
    assert [c["tool"] for c in tool_calls] == ["create_task"]
    assert tool_calls[0]["result"] is not None and tool_calls[0]["error"] is None

    # The mutation really landed, audited as the loop.
    task = db_session.execute(select(Task)).scalar_one()
    assert task.title == "Ship slice 2"
    task_events = (
        db_session.execute(
            select(ActivityEvent).where(ActivityEvent.entity_type == "task")
        )
        .scalars()
        .all()
    )
    assert task_events and all(e.actor == LOOP_ACTOR for e in task_events)

    # History survives a "reload" and feeds the next run as text turns.
    detail = client.get(f"/api/agent/conversations/{conversation_id}").json()
    assert detail["title"] == "Create a task called Ship slice 2"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    followup = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "What did I ask you to do?"},
    )
    assert followup.status_code == 200
    prior_turns = provider.requests[-1]["messages"]
    assert [m["role"] for m in prior_turns] == ["system", "user", "assistant", "user"]
    assert prior_turns[1]["content"] == "Create a task called Ship slice 2"
    assert not any("tool" in (m.get("role") or "") for m in prior_turns)


def test_provider_failure_before_any_tool_is_502_with_a_truthful_turn(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    """A first-call provider failure: 502, and a persisted assistant turn that
    honestly records the failure (no reply, no tool calls)."""
    _use_loop(ScriptedProvider([ProviderRequestError("connect timeout")]))
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Anyone home?"},
    )
    assert response.status_code == 502

    messages = client.get(f"/api/agent/conversations/{conversation_id}").json()[
        "messages"
    ]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Anyone home?"
    assistant = messages[1]
    assert assistant["content"] is None
    assert assistant["stop_reason"] == "provider_error"
    assert assistant["tool_calls"] is None


def test_provider_failure_mid_run_records_the_tools_that_already_ran(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    """The core Issue-1 case: a tool commits, then the provider dies. The task
    stays committed (undoable via trash) AND the conversation truthfully records
    that create_task ran — the failure never hides what happened."""
    _use_loop(
        ScriptedProvider(
            [
                tool_calls_turn(("create_task", {"data": {"title": "Half-done"}})),
                ProviderRequestError("llama-server went away"),
            ]
        )
    )
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Create a task called Half-done"},
    )
    assert response.status_code == 502

    # The mutation committed independently and survives (as designed — undoable).
    tasks = db_session.execute(select(Task)).scalars().all()
    assert [t.title for t in tasks] == ["Half-done"]

    # The conversation records the truth: the create_task that ran, then failure.
    assistant = client.get(f"/api/agent/conversations/{conversation_id}").json()[
        "messages"
    ][1]
    assert assistant["stop_reason"] == "provider_error"
    assert assistant["content"] is None
    assert [c["tool"] for c in assistant["tool_calls"]] == ["create_task"]
    assert assistant["tool_calls"][0]["error"] is None


def test_unexpected_tool_exception_is_500_with_the_partial_trajectory(
    client: TestClient,
    tool_db: sessionmaker[Session],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool completes, then the next dispatch dies with an OperationalError:
    the route answers 500 (not an unhandled crash) and the user turn is paired
    with an internal_error assistant turn carrying both calls (#103)."""
    real = registry.call_tool

    def call_tool(name: str, arguments: dict[str, object], *, actor: str) -> object:
        if name == "complete_task":
            raise OperationalError("UPDATE tasks", {}, Exception("database is locked"))
        return real(name, arguments, actor=actor)

    monkeypatch.setattr(registry, "call_tool", call_tool)
    _use_loop(
        ScriptedProvider(
            [
                tool_calls_turn(("create_task", {"data": {"title": "Committed"}})),
                tool_calls_turn(("complete_task", {"task_id": 1})),
                text_turn("never reached"),
            ]
        )
    )
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Create a task and complete it"},
        # The failure must be a typed HTTP error, not an exception escaping the app.
    )
    assert response.status_code == 500

    # The task the first call created stays committed and undoable.
    assert db_session.execute(select(Task.title)).scalars().all() == ["Committed"]

    messages = client.get(f"/api/agent/conversations/{conversation_id}").json()[
        "messages"
    ]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assistant = messages[1]
    assert assistant["stop_reason"] == "internal_error"
    assert assistant["content"] is None
    assert [c["tool"] for c in assistant["tool_calls"]] == [
        "create_task",
        "complete_task",
    ]
    assert assistant["tool_calls"][0]["error"] is None
    assert "internal error" in assistant["tool_calls"][1]["error"]


def test_run_over_budget_is_504_with_a_timed_out_turn(
    client: TestClient, tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that can't finish within the budget stops with a 504 and a truthful
    timed_out turn, rather than running on past the proxy/browser ceilings."""
    monkeypatch.setattr(get_settings(), "agent_run_budget_seconds", 0.0)
    # Budget 0 → the loop is already out of time before its first provider call,
    # so the provider is never even asked (script left intentionally empty).
    _use_loop(ScriptedProvider([]))
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Take your time"},
    )
    assert response.status_code == 504

    assistant = client.get(f"/api/agent/conversations/{conversation_id}").json()[
        "messages"
    ][1]
    assert assistant["stop_reason"] == "timed_out"
    assert assistant["content"] is None


def test_x_agent_actor_attributes_mutations_to_the_delegate(
    client: TestClient, tool_db: sessionmaker[Session], db_session: Session
) -> None:
    """A recognized ``X-Agent-Actor`` binds the run's audit actor (conductor)."""
    provider = ScriptedProvider(
        [
            tool_calls_turn(("create_task", {"data": {"title": "From conductor"}})),
            text_turn("Done."),
        ]
    )
    _use_loop(provider)

    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]
    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Create a task called From conductor"},
        headers={"X-Agent-Actor": "agent:conductor"},
    )
    assert response.status_code == 200

    task_events = (
        db_session.execute(
            select(ActivityEvent).where(ActivityEvent.entity_type == "task")
        )
        .scalars()
        .all()
    )
    assert task_events and all(e.actor == "agent:conductor" for e in task_events)


@pytest.mark.parametrize(
    "headers", [{}, {"X-Agent-Actor": "agent:bogus"}], ids=["absent", "unrecognized"]
)
def test_actor_falls_back_to_loop_when_header_absent_or_unrecognized(
    client: TestClient,
    tool_db: sessionmaker[Session],
    db_session: Session,
    headers: dict[str, str],
) -> None:
    """Absent or unrecognized actor headers fall back to the loop's default
    identity rather than erroring (contract: apps ignore unknown actors)."""
    provider = ScriptedProvider(
        [
            tool_calls_turn(("create_task", {"data": {"title": "Default actor"}})),
            text_turn("Done."),
        ]
    )
    _use_loop(provider)

    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]
    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Create a task called Default actor"},
        headers=headers,
    )
    assert response.status_code == 200

    task_events = (
        db_session.execute(
            select(ActivityEvent).where(ActivityEvent.entity_type == "task")
        )
        .scalars()
        .all()
    )
    assert task_events and all(e.actor == LOOP_ACTOR for e in task_events)


def test_missing_and_soft_deleted_threads_404_across_endpoints(
    client: TestClient,
) -> None:
    """Both a never-existed id and a soft-deleted thread 404 on GET,
    POST-messages, and DELETE.

    The contract rule the conductor relies on: it recreates the thread and
    retries a message exactly once on 404, so a pruned/soft-deleted thread must
    be indistinguishable from one that never existed — 404, not 200 or 410.
    """
    # Never existed.
    missing_id = 999_999
    assert client.get(f"/api/agent/conversations/{missing_id}").status_code == 404
    assert (
        client.post(
            f"/api/agent/conversations/{missing_id}/messages",
            json={"content": "hi"},
        ).status_code
        == 404
    )
    assert client.delete(f"/api/agent/conversations/{missing_id}").status_code == 404

    # Soft-deleted: identical 404s, so conductor's retry recreates rather than
    # resurrecting a trashed thread.
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]
    assert (
        client.delete(f"/api/agent/conversations/{conversation_id}").status_code == 204
    )
    assert client.get(f"/api/agent/conversations/{conversation_id}").status_code == 404
    assert (
        client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            json={"content": "hi"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/agent/conversations/{conversation_id}").status_code == 404
    )


def test_post_message_is_rate_limited(
    client: TestClient,
    tool_db: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "agent_messages_per_min", 1)
    _use_loop(ScriptedProvider([text_turn("ok")]))
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]

    first = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "one"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "two"},
    )
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_rate_limited_first_message_leaves_the_conversation_usable(
    client: TestClient,
    tool_db: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inline ask creates a conversation, then posts its first message. When
    that post is rejected (429), the empty conversation stays retrievable and
    still accepts a message — the client's contract is to claim and retry into
    it rather than orphan it (issue #147)."""
    monkeypatch.setattr(get_settings(), "agent_messages_per_min", 1)
    _use_loop(ScriptedProvider([text_turn("ok"), text_turn("later")]))
    warmup_id = client.post("/api/agent/conversations", json={}).json()["id"]
    assert (
        client.post(
            f"/api/agent/conversations/{warmup_id}/messages", json={"content": "one"}
        ).status_code
        == 200
    )

    claimed_id = client.post("/api/agent/conversations", json={}).json()["id"]
    rejected = client.post(
        f"/api/agent/conversations/{claimed_id}/messages", json={"content": "two"}
    )
    assert rejected.status_code == 429

    detail = client.get(f"/api/agent/conversations/{claimed_id}")
    assert detail.status_code == 200
    assert detail.json()["messages"] == []

    monkeypatch.setattr(get_settings(), "agent_messages_per_min", 100)
    retried = client.post(
        f"/api/agent/conversations/{claimed_id}/messages", json={"content": "two"}
    )
    assert retried.status_code == 200
    assert (
        len(client.get(f"/api/agent/conversations/{claimed_id}").json()["messages"]) == 2
    )


def test_second_message_while_a_run_is_in_progress_gets_409(
    client: TestClient,
    tool_db: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message posted while the same conversation is mid-run is rejected with
    409 (not run against overlapping history). Simulated by holding the
    conversation's run lock while the request is in flight — the endpoint runs
    in a worker thread, so its acquire genuinely blocks and times out."""
    monkeypatch.setattr(get_settings(), "agent_run_budget_seconds", 0.05)
    _use_loop(ScriptedProvider([text_turn("hi")]))
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]

    with conversation_locks.conversation_run_lock(conversation_id, wait_seconds=1.0):
        response = client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            json={"content": "are you busy?"},
        )
    assert response.status_code == 409
