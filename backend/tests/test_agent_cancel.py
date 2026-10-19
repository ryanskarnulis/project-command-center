"""Stopping a running agent run (#324).

Loop level: the cancel event is honoured at the same two boundaries as the
deadline — before each provider call and before each tool dispatch — and a
cancelled run *returns* its truthful partial trajectory (stop reason
``cancelled``) instead of raising. Route level: ``POST …/cancel`` reaches the
run actually in flight, the stopped run's own POST returns 200 with the
``cancelled`` turn persisted, and a Stop never leaks into a later run.
"""

from __future__ import annotations

import threading
from collections.abc import Generator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.loop import AgentLoop
from app.ai.providers.llamacpp import ChatResult, ToolSpec
from app.api import routes_agent, run_cancel
from app.db.models import ActivityEvent, ConversationMessage, Task
from app.main import app
from app.tools import registry, runtime
from tests.scripted_provider import ScriptedProvider
from tests.scripted_provider import text_turn as _text
from tests.scripted_provider import tool_calls_turn as _calls


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


class _StopDuringChat(ScriptedProvider):
    """Sets ``cancel`` while the model call ``on_call`` (1-based) is in flight —
    the user pressing Stop mid-generation."""

    def __init__(
        self,
        turns: Sequence[ChatResult | Exception],
        cancel: threading.Event,
        on_call: int = 1,
    ) -> None:
        super().__init__(turns)
        self._cancel = cancel
        self._on_call = on_call

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        result = super().chat(messages, tools=tools, timeout=timeout)
        if len(self.requests) == self._on_call:
            self._cancel.set()
        return result


# --- Loop ---------------------------------------------------------------------


def test_cancel_between_iterations_stops_before_the_next_provider_call(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop lands while a tool runs: the tool finishes (never interrupted), then
    the run ends before asking the model again — the tool's write stays."""
    cancel = threading.Event()
    real = registry.call_tool

    def call_tool(name: str, arguments: dict[str, object], *, actor: str) -> object:
        outcome = real(name, arguments, actor=actor)
        cancel.set()
        return outcome

    monkeypatch.setattr(registry, "call_tool", call_tool)
    provider = ScriptedProvider(
        [
            _calls(("create_task", {"data": {"title": "Ran before Stop"}})),
            _text("never reached"),
        ]
    )
    result = AgentLoop(provider).run("Make a task", cancel=cancel)

    assert result.stop_reason == "cancelled"
    assert result.reply is None
    assert len(provider.requests) == 1  # the second model turn never started
    assert result.iterations == 1
    assert [(r.tool, r.error) for r in result.tool_calls] == [("create_task", None)]
    with tool_db() as db:
        assert db.execute(select(Task.title)).scalars().all() == ["Ran before Stop"]
        # Still audited — and so still undoable from the trash.
        assert db.execute(select(ActivityEvent.action)).scalars().all() == ["created"]


def test_cancel_before_a_tool_call_skips_it(tool_db: sessionmaker[Session]) -> None:
    """Stop lands while the model is generating a tool batch: the call it
    proposes is recorded as not run and never reaches the service layer."""
    cancel = threading.Event()
    provider = _StopDuringChat(
        [
            _calls(
                ("create_task", {"data": {"title": "First"}}),
                ("create_task", {"data": {"title": "Second"}}),
            )
        ],
        cancel,
    )
    result = AgentLoop(provider).run("Make two tasks", cancel=cancel)

    assert result.stop_reason == "cancelled"
    assert [r.result for r in result.tool_calls] == [None, None]
    assert all("stopped" in str(r.error) for r in result.tool_calls)
    # Every proposed call is still answered, so the transcript stays well-formed.
    assert sum(1 for m in result.messages if m["role"] == "tool") == 2
    with tool_db() as db:
        assert db.execute(select(Task)).scalars().all() == []


def test_cancel_mid_batch_keeps_the_calls_that_already_ran(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel = threading.Event()
    real = registry.call_tool

    def call_tool(name: str, arguments: dict[str, object], *, actor: str) -> object:
        outcome = real(name, arguments, actor=actor)
        cancel.set()  # Stop arrives during the first call of the batch.
        return outcome

    monkeypatch.setattr(registry, "call_tool", call_tool)
    provider = ScriptedProvider(
        [
            _calls(
                ("create_task", {"data": {"title": "First"}}),
                ("create_task", {"data": {"title": "Second"}}),
            )
        ]
    )
    result = AgentLoop(provider).run("Make two tasks", cancel=cancel)

    assert result.stop_reason == "cancelled"
    assert [r.error is None for r in result.tool_calls] == [True, False]
    with tool_db() as db:
        assert db.execute(select(Task.title)).scalars().all() == ["First"]


def test_cancel_set_before_the_run_never_calls_the_provider(
    tool_db: sessionmaker[Session],
) -> None:
    cancel = threading.Event()
    cancel.set()
    provider = ScriptedProvider([])
    result = AgentLoop(provider).run("Hi", cancel=cancel)

    assert result.stop_reason == "cancelled"
    assert result.iterations == 0
    assert provider.requests == []


def test_stop_during_the_final_reply_lets_the_reply_stand(
    tool_db: sessionmaker[Session],
) -> None:
    """A Stop that lands while the model is writing its answer has no boundary
    left to stop at: the in-flight call finishes and the run completed."""
    cancel = threading.Event()
    provider = _StopDuringChat([_text("All done.")], cancel)
    result = AgentLoop(provider).run("Hi", cancel=cancel)

    assert result.stop_reason == "completed"
    assert result.reply == "All done."


# --- Route --------------------------------------------------------------------


class _GatedProvider(ScriptedProvider):
    """Parks the first model call until released, like a slow local model."""

    def __init__(self, turns: Sequence[ChatResult | Exception]) -> None:
        super().__init__(turns)
        self.in_call = threading.Event()
        self.release = threading.Event()

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        if not self.requests:
            self.in_call.set()
            assert self.release.wait(timeout=10.0)
        return super().chat(messages, tools=tools, timeout=timeout)


@pytest.fixture
def _file_tools(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    monkeypatch.setattr(runtime, "session_factory", session_factory)
    yield
    app.dependency_overrides.pop(routes_agent.get_agent_loop, None)


def test_cancel_endpoint_stops_the_in_flight_run_with_a_truthful_200(
    file_client: TestClient,
    session_factory: sessionmaker[Session],
    _file_tools: None,
) -> None:
    conversation_id = file_client.post("/api/agent/conversations", json={}).json()["id"]
    provider = _GatedProvider(
        [_calls(("create_task", {"data": {"title": "Proposed after Stop"}}))]
    )
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)

    posted: dict[str, Any] = {}

    def post() -> None:
        response = file_client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            json={"content": "make a task"},
        )
        posted["status"] = response.status_code
        posted["body"] = response.json()

    runner = threading.Thread(target=post)
    runner.start()
    try:
        assert provider.in_call.wait(timeout=5.0), "the run never reached the model"
        first = file_client.post(f"/api/agent/conversations/{conversation_id}/cancel")
        again = file_client.post(f"/api/agent/conversations/{conversation_id}/cancel")
    finally:
        provider.release.set()
        runner.join(timeout=15.0)

    assert not runner.is_alive()
    assert first.status_code == 200 and first.json() == {"cancel_requested": True}
    assert again.json() == {"cancel_requested": True}  # idempotent while running

    assert posted["status"] == 200
    assistant = posted["body"]["assistant_message"]
    assert assistant["stop_reason"] == "cancelled"
    assert assistant["content"] is None
    assert [c["tool"] for c in assistant["tool_calls"]] == ["create_task"]
    assert assistant["tool_calls"][0]["result"] is None

    with session_factory() as db:
        assert db.execute(select(Task)).scalars().all() == []
        rows = (
            db.execute(
                select(ConversationMessage.stop_reason)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.id)
            )
            .scalars()
            .all()
        )
        assert rows == [None, "cancelled"]
    # The run unregistered itself on the way out.
    assert run_cancel._ACTIVE == {}


def test_stale_cancel_does_not_stop_the_next_run(
    file_client: TestClient, _file_tools: None
) -> None:
    """A Stop with nothing running — e.g. one that lands just after a run
    finished — reports so and leaves nothing behind for the next run."""
    conversation_id = file_client.post("/api/agent/conversations", json={}).json()["id"]
    provider = ScriptedProvider([_text("first"), _text("second")])
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)

    url = f"/api/agent/conversations/{conversation_id}"
    assert file_client.post(f"{url}/messages", json={"content": "one"}).status_code == 200

    late = file_client.post(f"{url}/cancel")
    assert late.status_code == 200
    assert late.json() == {"cancel_requested": False}

    second = file_client.post(f"{url}/messages", json={"content": "two"})
    assert second.status_code == 200
    assert second.json()["assistant_message"]["stop_reason"] == "completed"
    assert second.json()["assistant_message"]["content"] == "second"


def test_cancel_does_not_leak_into_the_next_run_after_a_stopped_one(
    file_client: TestClient, _file_tools: None
) -> None:
    """The event that stopped run A belongs to run A: run B starts clean."""
    conversation_id = file_client.post("/api/agent/conversations", json={}).json()["id"]
    provider = _GatedProvider([_text("ignored"), _text("run B finished")])
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)
    url = f"/api/agent/conversations/{conversation_id}"

    runner = threading.Thread(
        target=lambda: file_client.post(f"{url}/messages", json={"content": "A"})
    )
    runner.start()
    try:
        assert provider.in_call.wait(timeout=5.0)
        assert file_client.post(f"{url}/cancel").json() == {"cancel_requested": True}
    finally:
        provider.release.set()
        runner.join(timeout=15.0)

    # Run A's in-flight call returned a text reply, so it completed; either
    # way, run B must not inherit the Stop.
    second = file_client.post(f"{url}/messages", json={"content": "B"})
    assert second.status_code == 200
    assert second.json()["assistant_message"]["stop_reason"] == "completed"
    assert second.json()["assistant_message"]["content"] == "run B finished"


def test_cancel_404s_for_missing_and_deleted_conversations(client: TestClient) -> None:
    assert client.post("/api/agent/conversations/999999/cancel").status_code == 404

    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]
    assert client.delete(f"/api/agent/conversations/{conversation_id}").status_code == 204
    assert (
        client.post(f"/api/agent/conversations/{conversation_id}/cancel").status_code
        == 404
    )


def test_cancel_with_nothing_running_is_a_no_op(client: TestClient) -> None:
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]
    response = client.post(f"/api/agent/conversations/{conversation_id}/cancel")
    assert response.status_code == 200
    assert response.json() == {"cancel_requested": False}
    assert run_cancel._ACTIVE == {}
