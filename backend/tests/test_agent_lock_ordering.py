"""Lock ordering on the agent message endpoint (#91).

The conversation run lock and SQLite's write lock have to be taken in that
order. When the endpoint took the write lock first — its ``get_db_write``
session answered the 404 check *before* the wait — two messages on the same
conversation inverted them: the waiting follower held the write lock, and the
running leader's mutating tool then blocked on it until ``busy_timeout`` and
failed with "database is locked".

Runs on ``file_client`` (real file, WAL, NullPool); the ``:memory:`` +
StaticPool fixture shares one connection and cannot express the race.
"""

from __future__ import annotations

import threading
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai.loop import AgentRunResult, ToolCallRecord
from app.api import routes_agent
from app.main import app
from app.services import tasks as tasks_service
from app.tools import runtime

# The leader waits this long for the follower's request to land, then does its
# tool write. Comfortably under the 5s busy_timeout, so a failure here is the
# lock inversion and not a slow machine.
FOLLOWER_ARRIVAL_SECONDS = 2.0


@pytest.fixture
def file_tool_db(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> sessionmaker[Session]:
    """Point the loop's per-tool-call sessions at the file engine."""
    monkeypatch.setattr(runtime, "session_factory", session_factory)
    return session_factory


class _WritingLoop:
    """Stands in for ``AgentLoop``: waits for the follower, then writes.

    The write goes through ``runtime.tool_session`` — the same BEGIN IMMEDIATE
    session a real mutating tool uses — which is the thing that used to fail.
    """

    def __init__(self, running: threading.Event, follower_arrived: threading.Event):
        self._running = running
        self._follower_arrived = follower_arrived
        self.error: BaseException | None = None

    def run(
        self, content: str, *, history: object, actor: str, deadline: float
    ) -> AgentRunResult:
        self._running.set()
        self._follower_arrived.wait(timeout=FOLLOWER_ARRIVAL_SECONDS)
        try:
            with runtime.tool_session("create_task") as db:
                tasks_service.create_task(
                    db, project_id=None, title="written mid-run"
                )
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            self.error = exc
            raise
        return AgentRunResult(
            reply="done",
            stop_reason="completed",
            tool_calls=[
                ToolCallRecord(tool="create_task", arguments={}, result="{}")
            ],
            iterations=1,
            messages=[],
        )


@pytest.fixture
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.pop(routes_agent.get_agent_loop, None)


def test_a_second_message_does_not_block_the_first_runs_tool_write(
    file_client: TestClient,
    file_engine: Engine,
    file_tool_db: sessionmaker[Session],
    _clear_overrides: None,
) -> None:
    """The follower must wait on the conversation lock without holding the write lock.

    Before the fix the follower's ``get_db_write`` session read the conversation
    on arrival, took the write lock, and blocked on the conversation lock — so
    the leader's tool write died on ``busy_timeout`` after ~5s.
    """
    conversation_id = file_client.post("/api/agent/conversations", json={}).json()["id"]

    running = threading.Event()
    follower_arrived = threading.Event()
    loop = _WritingLoop(running, follower_arrived)
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: loop

    responses: dict[str, int] = {}

    def post(label: str, content: str) -> None:
        responses[label] = file_client.post(
            f"/api/agent/conversations/{conversation_id}/messages",
            json={"content": content},
        ).status_code

    leader = threading.Thread(target=post, args=("leader", "first"))
    leader.start()
    assert running.wait(timeout=5.0), "leader never entered its run"

    follower = threading.Thread(target=post, args=("follower", "second"))
    follower.start()
    # No way to observe "the follower is parked on the lock" from outside, so
    # give it a moment to get there; on the broken build that is long enough for
    # it to have taken the write lock.
    follower_arrived.wait(timeout=0.3)
    follower_arrived.set()

    leader.join(timeout=15.0)
    follower.join(timeout=15.0)
    assert not leader.is_alive() and not follower.is_alive()

    assert loop.error is None, f"the run's tool write failed: {loop.error!r}"
    assert responses["leader"] == 200
    # The follower either ran after the leader (200) or gave up on the wait
    # (409) — both are correct; deadlocking the leader is not.
    assert responses["follower"] in {200, 409}
