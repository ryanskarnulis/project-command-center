"""Deleting a conversation is serialized against its active run (#149).

Before the fix, ``delete_conversation`` never took ``conversation_run_lock``.
Once the user-turn commit released SQLite's write lock, a DELETE could
soft-delete the thread while the model was still generating; the run then
committed its assistant turn into the deleted thread, so the caller got a
successful exchange that GET answered 404 for — the tool-call trajectory (or
failure record) silently vanished.

The contract: DELETE gets **409** while a run is in flight. Runs on
``file_client`` — the ``:memory:`` + StaticPool fixture shares one connection
and cannot express the race.
"""

from __future__ import annotations

import threading
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.ai.loop import AgentRunResult
from app.api import routes_agent
from app.db.models import Conversation, ConversationMessage
from app.main import app


class _BlockedLoop:
    """A provider parked on an event, standing in for a long model call."""

    def __init__(self) -> None:
        self.running = threading.Event()
        self.release = threading.Event()

    def run(
        self, content: str, *, history: object, actor: str, deadline: float
    ) -> AgentRunResult:
        self.running.set()
        self.release.wait(timeout=10.0)
        return AgentRunResult(
            reply="finished",
            stop_reason="completed",
            tool_calls=[],
            iterations=1,
            messages=[],
        )


@pytest.fixture
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.pop(routes_agent.get_agent_loop, None)


def test_delete_during_a_run_is_rejected_and_the_run_survives(
    file_client: TestClient,
    session_factory: sessionmaker[Session],
    _clear_overrides: None,
) -> None:
    conversation_id = file_client.post("/api/agent/conversations", json={}).json()["id"]

    loop = _BlockedLoop()
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: loop

    post_status: dict[str, int] = {}

    def post() -> None:
        post_status["code"] = file_client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            json={"content": "run now"},
        ).status_code

    runner = threading.Thread(target=post)
    runner.start()
    try:
        assert loop.running.wait(timeout=5.0), "the run never started"
        # The user turn is committed by now; this is exactly the window the
        # issue's reproduction exploited.
        deleted = file_client.delete(f"/api/agent/conversations/{conversation_id}")
    finally:
        loop.release.set()
        runner.join(timeout=15.0)

    assert not runner.is_alive()
    assert deleted.status_code == 409
    assert post_status["code"] == 200

    # The run's outcome is readable, not buried in a deleted thread.
    detail = file_client.get(f"/api/agent/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert [m["role"] for m in detail.json()["messages"]] == ["user", "assistant"]

    with session_factory() as db:
        conversation = db.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.deleted_at is None
        rows = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.id)
            .all()
        )
        assert [(m.role, m.content) for m in rows] == [
            ("user", "run now"),
            ("assistant", "finished"),
        ]


def test_delete_succeeds_once_the_run_has_finished(
    file_client: TestClient, _clear_overrides: None
) -> None:
    """The lock is released with the run, so an idle conversation deletes normally."""
    conversation_id = file_client.post("/api/agent/conversations", json={}).json()["id"]

    loop = _BlockedLoop()
    loop.release.set()
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: loop

    assert (
        file_client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            json={"content": "run now"},
        ).status_code
        == 200
    )
    assert (
        file_client.delete(f"/api/agent/conversations/{conversation_id}").status_code
        == 204
    )
    assert (
        file_client.get(f"/api/agent/conversations/{conversation_id}").status_code == 404
    )
