"""Agent loop end-to-end against a scripted provider (no GPU).

``ScriptedProvider`` plays the model: each ``chat()`` call pops the next
scripted turn (a ``ChatResult``, or an exception to raise), so the full loop
path — registry dispatch, Pydantic argument validation, session/actor
plumbing, self-correction — runs against the real service layer and an
in-memory database. The live model is exercised by the eval harness (slice 4),
not here.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from typing import Any

import pytest
import structlog
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.loop import LOOP_ACTOR, AgentLoop
from app.ai.providers.llamacpp import (
    ChatResult,
    ToolCall,
    ToolCallArgumentsError,
    ToolSpec,
)
from app.db.models import ActivityEvent, Task, TaskWorkflowStatus
from app.mcp.server import mcp
from app.tools import registry, runtime


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    """Point the tool bodies' per-call sessions at the test engine."""
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def _text(content: str) -> ChatResult:
    return ChatResult(content=content, tool_calls=[], finish_reason="stop", usage=None)


def _calls(*calls: tuple[str, dict[str, Any]]) -> ChatResult:
    return ChatResult(
        content=None,
        tool_calls=[
            ToolCall(id=f"call_{index}", name=name, arguments=arguments)
            for index, (name, arguments) in enumerate(calls)
        ],
        finish_reason="tool_calls",
        usage=None,
    )


class ScriptedProvider:
    """Pops one scripted turn per ``chat()`` call; records every request."""

    def __init__(self, turns: Sequence[ChatResult | Exception]) -> None:
        self._turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.requests.append({"messages": list(messages), "tools": list(tools or [])})
        assert self._turns, "provider called more times than scripted"
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def test_create_complete_flow_end_to_end(tool_db: sessionmaker[Session]) -> None:
    """create project → create task → complete, all audited as agent:loop."""
    provider = ScriptedProvider(
        [
            _calls(("create_project", {"data": {"name": "Agent epic"}})),
            _calls(("create_task", {"data": {"title": "Ship slice 1", "project_id": 1}})),
            _calls(("complete_task", {"task_id": 1})),
            _text("Created and completed 'Ship slice 1'."),
        ]
    )
    result = AgentLoop(provider).run(
        "Create a project 'Agent epic' with a task 'Ship slice 1' and finish it."
    )

    assert result.stop_reason == "completed"
    assert result.reply == "Created and completed 'Ship slice 1'."
    assert result.iterations == 4
    assert [record.tool for record in result.tool_calls] == [
        "create_project",
        "create_task",
        "complete_task",
    ]
    assert all(record.error is None for record in result.tool_calls)

    # Every provider turn was offered the full registry — and never a hard delete.
    offered = {spec.name for spec in provider.requests[0]["tools"]}
    assert {"create_task", "complete_task", "search", "restore_task"} <= offered
    assert not [name for name in offered if "purge" in name or "empty" in name]

    # Each tool result went back to the model as a role:tool message.
    followup = provider.requests[1]["messages"]
    assert followup[-1]["role"] == "tool"
    assert "Agent epic" in followup[-1]["content"]

    # DB end-state + audit rows.
    with tool_db() as db:
        task = db.execute(select(Task)).scalar_one()
        assert task.title == "Ship slice 1"
        assert task.workflow_status == TaskWorkflowStatus.done
        events = db.execute(select(ActivityEvent)).scalars().all()
        assert {event.action for event in events} >= {"created", "completed"}
        assert all(event.actor == LOOP_ACTOR for event in events)


def test_invalid_arguments_are_fed_back_and_corrected(
    tool_db: sessionmaker[Session],
) -> None:
    """A validation failure never reaches the service layer; the model retries."""
    provider = ScriptedProvider(
        [
            _calls(("create_project", {"data": {"name": "Fixups"}})),
            _calls(("create_task", {"data": {"title": "   ", "project_id": 1}})),
            _calls(("create_task", {"data": {"title": "Corrected", "project_id": 1}})),
            _text("Done."),
        ]
    )
    result = AgentLoop(provider).run("Add a task to Fixups.")

    assert result.stop_reason == "completed"
    failed = result.tool_calls[1]
    assert failed.result is None
    assert failed.error is not None and "title" in failed.error

    # The rejection reached the model as a tool-result error message…
    retry_request = provider.requests[2]["messages"]
    assert retry_request[-1]["role"] == "tool"
    assert "Invalid arguments" in retry_request[-1]["content"]

    # …and only the corrected call landed.
    with tool_db() as db:
        assert db.execute(select(Task.title)).scalars().all() == ["Corrected"]


def test_unparseable_tool_call_corrected_via_user_message(
    tool_db: sessionmaker[Session],
) -> None:
    """ToolCallArgumentsError from the provider is fed back, chess-style."""
    provider = ScriptedProvider(
        [
            ToolCallArgumentsError("create_task", "arguments are not valid JSON"),
            _text("Never mind."),
        ]
    )
    result = AgentLoop(provider).run("Add a task.")

    assert result.stop_reason == "completed"
    assert result.tool_calls == []  # nothing was dispatched
    retry_request = provider.requests[1]["messages"]
    assert retry_request[-1]["role"] == "user"
    assert "create_task" in retry_request[-1]["content"]


def test_domain_errors_do_not_consume_the_correction_budget(
    tool_db: sessionmaker[Session],
) -> None:
    """A well-formed call the service rejects is an ordinary observation."""
    provider = ScriptedProvider(
        [
            _calls(("complete_task", {"task_id": 99999})),
            _text("Task 99999 does not exist."),
        ]
    )
    # max_corrections=0 proves the not-found feedback isn't billed as a correction.
    result = AgentLoop(provider, max_corrections=0).run("Finish task 99999.")

    assert result.stop_reason == "completed"
    assert result.tool_calls[0].error is not None
    assert "not found" in result.tool_calls[0].error
    feedback = provider.requests[1]["messages"][-1]
    assert feedback["role"] == "tool"
    assert "not found" in feedback["content"]


def test_unknown_tool_is_a_schema_error(tool_db: sessionmaker[Session]) -> None:
    provider = ScriptedProvider(
        [
            _calls(("summon_intern", {})),
            _text("No such tool; stopping."),
        ]
    )
    result = AgentLoop(provider).run("Do something odd.")

    assert result.stop_reason == "completed"
    assert result.tool_calls[0].error is not None
    assert "Unknown tool" in result.tool_calls[0].error


def test_correction_limit_terminates_the_loop(tool_db: sessionmaker[Session]) -> None:
    provider = ScriptedProvider(
        [
            _calls(("create_task", {"data": {"title": ""}})),
            _calls(("create_task", {"data": {"title": ""}})),
        ]
    )
    result = AgentLoop(provider, max_corrections=1).run("Add a task.")

    assert result.stop_reason == "correction_limit"
    assert result.reply is None
    assert len(provider.requests) == 2
    with tool_db() as db:
        assert db.execute(select(Task)).scalars().all() == []


def test_max_iterations_terminates_the_loop(tool_db: sessionmaker[Session]) -> None:
    provider = ScriptedProvider(
        [
            _calls(("list_projects", {})),
            _calls(("list_projects", {})),
        ]
    )
    result = AgentLoop(provider, max_iterations=2).run("Browse forever.")

    assert result.stop_reason == "max_iterations"
    assert result.iterations == 2
    assert len(provider.requests) == 2


def test_tool_session_reuses_a_bound_request_id(
    tool_db: sessionmaker[Session],
) -> None:
    """The loop binds one request ID per run; tool calls must not clobber it."""
    structlog.contextvars.bind_contextvars(request_id="run-1")
    try:
        with runtime.tool_session("list_projects"):
            assert structlog.contextvars.get_contextvars()["request_id"] == "run-1"
        assert structlog.contextvars.get_contextvars()["request_id"] == "run-1"
    finally:
        structlog.contextvars.unbind_contextvars("request_id")


def test_registry_and_mcp_expose_identical_tools() -> None:
    """The registry is the single source of truth: same names, same schemas."""
    # _tool_manager is FastMCP-internal, but this is exactly the parity the
    # refactor must guarantee, so the test reaches in.
    mcp_tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    specs = {spec.name: spec for spec in registry.tool_specs()}

    assert set(specs) == set(mcp_tools)
    for name, spec in specs.items():
        assert spec.parameters == mcp_tools[name].parameters, name
        assert spec.description  # never advertise an undocumented tool
