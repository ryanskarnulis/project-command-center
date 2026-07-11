"""MCP server happy path, driven through the real protocol layer.

``mcp_client`` speaks JSON-RPC to the FastMCP server over an in-memory
transport — the same request/validation/serialization path a real client
(Claude Code over stdio) exercises, minus the pipe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.mcp import runtime
from app.mcp.server import mcp
from app.services import activity


@pytest.fixture
def _mcp_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    """Point the MCP tools' per-call sessions at the test engine."""
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


@asynccontextmanager
async def mcp_client() -> AsyncIterator[ClientSession]:
    """In-memory client session; enter inside the test body, not a fixture.

    anyio cancel scopes must be exited in the task that entered them, and a
    pytest fixture's teardown runs in a different task — so this cannot be an
    async fixture.
    """
    async with create_connected_server_and_client_session(
        mcp._mcp_server
    ) as client:
        yield client


async def _call(client: ClientSession, tool: str, args: dict[str, Any]) -> Any:
    """Call a tool, assert success, return its structured (or text) payload."""
    result = await client.call_tool(tool, args)
    assert not result.isError, f"{tool} failed: {result.content}"
    if result.structuredContent is not None:
        return result.structuredContent
    assert result.content and result.content[0].type == "text"
    return result.content[0].text


async def test_no_destructive_tools_registered(
    _mcp_db: sessionmaker[Session],
) -> None:
    """The hard-delete guardrail is structural: no purge/empty tool exists."""
    async with mcp_client() as client:
        tools = await client.list_tools()
    names = {t.name for t in tools.tools}
    assert names, "server exposed no tools"
    assert not [n for n in names if "purge" in n or "empty" in n]


async def test_task_lifecycle_with_agent_attribution(
    _mcp_db: sessionmaker[Session],
) -> None:
    """create → list → complete → trash → restore, all audited as agent:mcp."""
    async with mcp_client() as client:
        project = await _call(client, "create_project", {"data": {"name": "MCP e2e"}})
        project_id = project["id"]

        task = await _call(
            client,
            "create_task",
            {"data": {"title": "Wire the MCP server", "project_id": project_id}},
        )
        task_id = task["id"]
        assert task["workflow_status"] == "open"

        listed = await _call(client, "list_tasks", {"project_id": project_id})
        assert [t["id"] for t in listed["result"]] == [task_id]

        done = await _call(client, "complete_task", {"task_id": task_id})
        assert done["workflow_status"] == "done"

        trashed_msg = await _call(client, "trash_task", {"task_id": task_id})
        assert "trash" in trashed_msg["result"]
        trash = await _call(client, "list_trash", {})
        assert task_id in [t["id"] for t in trash["tasks"]]

        restored = await _call(client, "restore_task", {"task_id": task_id})
        assert restored["id"] == task_id
        assert restored["deleted_at"] is None

        events = await _call(client, "list_activity", {"project_id": project_id})
        actions = {e["action"] for e in events["result"]}
        assert {"created", "completed", "deleted", "restored"} <= actions
        assert all(e["actor"] == "agent:mcp" for e in events["result"])


async def test_invalid_arguments_are_rejected_as_tool_errors(
    _mcp_db: sessionmaker[Session],
) -> None:
    """Boundary validation: a blank title never reaches the service layer."""
    async with mcp_client() as client:
        result = await client.call_tool("create_task", {"data": {"title": "   "}})
        assert result.isError

        missing = await client.call_tool("complete_task", {"task_id": 99999})
        assert missing.isError
        assert missing.content[0].type == "text"
        assert "not found" in missing.content[0].text


def test_record_event_defaults_to_user_actor(db_session: Session) -> None:
    """Without a bound actor the event stays NULL — attributed to the user."""
    event = activity.record_event(
        db_session,
        project_id=None,
        entity_type="task",
        entity_id=1,
        action="created",
        summary="x",
    )
    assert event.actor is None

    token = activity.current_actor.set("agent:mcp")
    try:
        stamped = activity.record_event(
            db_session,
            project_id=None,
            entity_type="task",
            entity_id=1,
            action="updated",
            summary="y",
        )
    finally:
        activity.current_actor.reset(token)
    assert stamped.actor == "agent:mcp"
