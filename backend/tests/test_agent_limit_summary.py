"""When a run stops at a limit, it says what it did (#399).

Against the scripted provider: every limit stop with a call that ran gets one
tool-free summary call that reads the code-built run record; a run where
nothing ran, a Stop, and a summary that fails or is cut off all keep today's
behavior (no reply, the client's stop line).
"""

from __future__ import annotations

import inspect
import json
import threading
import time
from collections.abc import Generator, Sequence
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import loop as loop_module
from app.ai import run_record
from app.ai.loop import AgentLoop, ToolCallRecord
from app.ai.providers.llamacpp import (
    ChatResult,
    ProviderRequestError,
    ToolCall,
    ToolSpec,
)
from app.api import routes_agent
from app.db.models import Task
from app.main import app
from app.tools import registry, runtime
from tests.scripted_provider import ScriptedProvider
from tests.scripted_provider import text_turn as _text
from tests.scripted_provider import tool_calls_turn as _calls

SUMMARY = "Made 'Alpha'; 'Beta' wasn't made — ask again to finish."


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def _alpha() -> tuple[str, dict[str, Any]]:
    return ("create_task", {"title": "Alpha"})


def _invalid() -> tuple[str, dict[str, Any]]:
    return ("create_task", {"title": ""})


def _cut_off(content: str = "and then I will also") -> ChatResult:
    return ChatResult(content=content, tool_calls=[], finish_reason="length", usage=None)


# Each limit stop, scripted so one write (task "Alpha") runs before it fires.
_LIMIT_RUNS: dict[str, tuple[list[ChatResult], dict[str, Any]]] = {
    "max_iterations": ([_calls(_alpha())], {"max_iterations": 1}),
    "correction_limit": ([_calls(_alpha(), _invalid())], {"max_corrections": 0}),
    "truncated": ([_calls(_alpha()), _cut_off()], {}),
    "no_progress": (
        [_calls(_alpha()), _calls(("list_projects", {})), _calls(("list_projects", {}))],
        {},
    ),
    "tool_call_limit": (
        [_calls(_alpha(), ("create_task", {"title": "Beta"}))],
        {"max_tool_calls": 1},
    ),
}


@pytest.mark.parametrize("stop_reason", sorted(_LIMIT_RUNS))
def test_limit_stop_saves_a_summary_of_what_ran(
    tool_db: sessionmaker[Session], stop_reason: str
) -> None:
    turns, options = _LIMIT_RUNS[stop_reason]
    provider = ScriptedProvider([*turns, _text(SUMMARY)])
    result = AgentLoop(provider, **options).run("Make tasks Alpha and Beta.")

    # The stop reason is unchanged — delegates and the UI read it.
    assert result.stop_reason == stop_reason
    assert result.reply == SUMMARY
    assert result.messages[-1] == {"role": "assistant", "content": SUMMARY}
    # Exactly one extra call, offered no tools, capped, and a fresh brief.
    assert len(provider.requests) == len(turns) + 1
    summary = provider.requests[-1]
    assert summary["tools"] == []
    assert summary["max_tokens"] == loop_module.SUMMARY_MAX_TOKENS
    assert [m["role"] for m in summary["messages"]] == ["system", "user"]
    brief = summary["messages"][1]["content"]
    assert "Make tasks Alpha and Beta." in brief
    assert 'Done:\n- #1 create_task {"title":"Alpha"} -> id 1 "Alpha"' in brief
    assert run_record.LIMIT_NOTE in brief
    assert run_record.LIMIT_REASONS[stop_reason] in brief
    # The write the summary describes is real.
    with tool_db() as db:
        assert db.execute(select(Task.title)).scalars().all() == ["Alpha"]


def test_every_limit_stop_has_a_reason_line() -> None:
    limits = {
        reason
        for reason in get_args(loop_module._StopReason)
        if reason not in loop_module._FAILURE_STATUS
        and reason not in ("completed", "cancelled")
    }
    assert limits == set(run_record.LIMIT_REASONS) == set(_LIMIT_RUNS)


@pytest.mark.parametrize(
    ("turns", "options", "stop_reason"),
    [
        # Every call malformed: nothing reached the service layer.
        ([_calls(_invalid()), _calls(_invalid())], {"max_corrections": 1}, "correction_limit"),
        # An unknown tool, then out of turns.
        ([_calls(("make_coffee", {}))], {"max_iterations": 1}, "max_iterations"),
        # Cut off before any tool ran.
        ([_cut_off()], {}, "truncated"),
    ],
)
def test_no_summary_call_when_nothing_ran(
    tool_db: sessionmaker[Session],
    turns: list[ChatResult],
    options: dict[str, Any],
    stop_reason: str,
) -> None:
    provider = ScriptedProvider(turns)
    result = AgentLoop(provider, **options).run("Add a task.")

    assert result.stop_reason == stop_reason
    assert result.reply is None
    assert len(provider.requests) == len(turns)


class _StopOnCall(ScriptedProvider):
    """Sets ``cancel`` while call ``on_call`` (1-based) is in flight."""

    def __init__(
        self, turns: Sequence[ChatResult | Exception], cancel: threading.Event, on_call: int
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
        result = super().chat(messages, tools=tools, max_tokens=max_tokens, timeout=timeout)
        if len(self.requests) == self._on_call:
            self._cancel.set()
        return result


def test_cancelled_run_keeps_no_reply_even_after_writes(
    tool_db: sessionmaker[Session],
) -> None:
    """The user asked it to stop: no summary call, as before."""
    cancel = threading.Event()
    provider = _StopOnCall(
        [_calls(_alpha()), _calls(("create_task", {"title": "Beta"}))],
        cancel,
        on_call=2,
    )
    result = AgentLoop(provider).run("Make tasks Alpha and Beta.", cancel=cancel)

    assert result.stop_reason == "cancelled"
    assert result.reply is None
    assert len(provider.requests) == 2


def test_stop_during_the_last_tool_skips_the_summary(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Stop pressed while the run's last tool runs lands after the last
    boundary, so the run still ends at its limit — but the user asked it to
    stop, so it makes no further model call."""
    cancel = threading.Event()
    original = registry.call_tool

    def call_then_stop(name: str, arguments: dict[str, Any], *, actor: str) -> Any:
        outcome = original(name, arguments, actor=actor)
        cancel.set()
        return outcome

    monkeypatch.setattr(registry, "call_tool", call_then_stop)
    provider = ScriptedProvider([_calls(_alpha())])
    result = AgentLoop(provider, max_iterations=1).run("Make Alpha.", cancel=cancel)

    assert result.stop_reason == "max_iterations"
    assert result.reply is None
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "summary_turn",
    [
        ProviderRequestError("connect timeout"),
        _cut_off("Made 'Alpha' and also 'Be"),
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="call_9", name="list_tasks", arguments={})],
            finish_reason="tool_calls",
            usage=None,
        ),
        _text("   "),
        RuntimeError("bug in the provider"),
    ],
    ids=["provider_error", "cut_off", "tool_call", "empty", "unexpected"],
)
def test_a_summary_that_fails_falls_back_to_no_reply(
    tool_db: sessionmaker[Session], summary_turn: ChatResult | Exception
) -> None:
    provider = ScriptedProvider([_calls(_alpha()), summary_turn])
    result = AgentLoop(provider, max_iterations=1).run("Make Alpha and Beta.")

    assert result.stop_reason == "max_iterations"
    assert result.reply is None
    # No fragment of a cut-off summary reaches the transcript.
    assert result.messages[-1]["role"] == "tool"
    assert [record.tool for record in result.tool_calls] == ["create_task"]


def test_summary_is_bounded_by_the_run_deadline(tool_db: sessionmaker[Session]) -> None:
    provider = ScriptedProvider([_calls(_alpha()), _text(SUMMARY)])
    deadline = time.monotonic() + 60
    result = AgentLoop(provider, max_iterations=1).run("Make Alpha.", deadline=deadline)

    assert result.reply == SUMMARY
    timeout = provider.requests[-1]["timeout"]
    assert timeout is not None and 0 < timeout <= 60


def test_no_summary_once_the_deadline_has_passed(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Time ran out between the last tool and the summary: no call at all."""
    now = [0.0]
    monkeypatch.setattr(loop_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    original = registry.call_tool

    def slow_call_tool(name: str, arguments: dict[str, Any], *, actor: str) -> Any:
        outcome = original(name, arguments, actor=actor)
        now[0] = 100.0  # the tool ran past the deadline
        return outcome

    monkeypatch.setattr(registry, "call_tool", slow_call_tool)
    provider = ScriptedProvider([_calls(_alpha())])
    result = AgentLoop(provider, max_iterations=1).run("Make Alpha.", deadline=10.0)

    assert result.stop_reason == "max_iterations"
    assert result.reply is None
    assert len(provider.requests) == 1


# --- The record ---------------------------------------------------------------


def test_record_sorts_calls_by_what_happened() -> None:
    records = [
        ToolCallRecord(tool="list_projects", arguments={}, result="[]"),
        ToolCallRecord(
            tool="create_task",
            arguments={"title": "Alpha"},
            result='{"id": 3, "title": "Alpha", "description": null}',
        ),
        ToolCallRecord(tool="complete_task", arguments={"task_id": 7}, error="Task 7 is blocked"),
        ToolCallRecord(
            tool="create_task",
            arguments={"title": ""},
            error=f"{run_record.INVALID_ARGUMENTS_PREFIX}title: too short",
        ),
        ToolCallRecord(tool="make_coffee", arguments={}, error="Unknown tool 'make_coffee'"),
        ToolCallRecord(
            tool="create_task",
            arguments={"title": "Beta"},
            error=loop_module._TOOL_CALL_LIMIT_ERROR,
        ),
        ToolCallRecord(
            tool="trash_task",
            arguments={"task_id": 4},
            result='Task 4 "Old" and its subtasks moved to trash',
        ),
    ]
    assert [run_record.classify(r) for r in records] == [
        "looked_up",
        "done",
        "refused",
        "not_run",
        "not_run",
        "not_run",
        "done",
    ]
    assert run_record.render(records) == (
        "Done:\n"
        '- #2 create_task {"title":"Alpha"} -> id 3 "Alpha"\n'
        '- #7 trash_task {"task_id":4} -> Task 4 "Old" and its subtasks moved to trash\n'
        "Refused:\n"
        '- #3 complete_task {"task_id":7} -> Task 7 is blocked\n'
        "Looked up:\n"
        "- #1 list_projects {}\n"
        "Not run:\n"
        '- #4 create_task {"title":""} -> Invalid arguments: title: too short\n'
        "- #5 make_coffee {} -> Unknown tool 'make_coffee'\n"
        '- #6 create_task {"title":"Beta"} -> '
        "not run: the agent run reached its limit of tool calls before this call"
    )


def test_record_says_nothing_was_done_outright() -> None:
    records = [ToolCallRecord(tool="search", arguments={"query": "x"}, result="{}")]
    assert run_record.render(records).startswith("Done:\n- nothing\nLooked up:")
    assert run_record.any_ran(records)
    assert not run_record.any_ran(
        [ToolCallRecord(tool="create_task", arguments={}, error=loop_module._SKIPPED_ERROR)]
    )


def test_record_lines_stay_compact() -> None:
    record = ToolCallRecord(
        tool="create_task",
        arguments={"title": "x" * 500},
        result='{"id": 1, "title": "' + "y" * 500 + '"}',
    )
    line = run_record.render([record]).splitlines()[1]
    assert len(line) < 400


def test_read_only_flag_agrees_with_the_session_each_body_opens() -> None:
    """A tool is ``read_only`` exactly when its body opens
    ``tool_session(..., write=False)``: the flag and the session mode cannot drift."""
    for tool in registry.all_tools():
        opens_read_session = "write=False" in inspect.getsource(tool.fn)
        assert tool.read_only == opens_read_session, tool.name
    assert run_record.READ_TOOLS == {t.name for t in registry.all_tools() if t.read_only}
    assert run_record.READ_TOOLS  # the derivation found the reads


def test_read_only_flag_is_not_in_any_emitted_schema() -> None:
    for tool in registry.all_tools():
        assert "read_only" not in json.dumps(tool.parameters)
    for spec in registry.tool_specs():
        assert "read_only" not in json.dumps(spec.parameters)
        assert "read_only" not in spec.description


# --- Route --------------------------------------------------------------------


def test_route_saves_the_summary_as_the_reply(
    client: TestClient, tool_db: sessionmaker[Session]
) -> None:
    """The saved turn carries the summary as content, the limit as its stop
    reason, and the next message's history includes it."""
    provider = ScriptedProvider(
        [
            _calls(_alpha()),
            _calls(("list_tasks", {})),
            _calls(("list_tasks", {})),
            _text(SUMMARY),
            _text("Anything else?"),
        ]
    )
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(provider)
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Make tasks Alpha and Beta."},
    )
    assert response.status_code == 200
    assistant = response.json()["assistant_message"]
    assert assistant["content"] == SUMMARY
    assert assistant["stop_reason"] == "no_progress"
    assert [call["tool"] for call in assistant["tool_calls"]] == [
        "create_task",
        "list_tasks",
        "list_tasks",
    ]

    saved = client.get(f"/api/agent/conversations/{conversation_id}").json()["messages"]
    assert saved[-1]["content"] == SUMMARY
    assert saved[-1]["stop_reason"] == "no_progress"

    client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "Thanks."},
    )
    history = provider.requests[-1]["messages"]
    assert {"role": "assistant", "content": SUMMARY} in history
