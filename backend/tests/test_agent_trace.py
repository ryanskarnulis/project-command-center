"""The run trace: one JSONL record per agent run (#400).

Against the scripted provider: a traced run writes exactly one well-formed
record on every exit — completed, each limit, a Stop, a provider failure, the
deadline, an internal crash — with one entry per model call; a write that
fails never costs the run; tracing is off unless ``PCC_TRACE_PATH`` is set;
and the reader script reads what the loop writes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import context_budget
from app.ai import loop as loop_module
from app.ai import trace as run_trace
from app.ai.loop import (
    SUMMARY_PROMPT_SHA256,
    SYSTEM_PROMPT_SHA256,
    AgentLoop,
    AgentRunFailed,
)
from app.ai.providers.llamacpp import (
    ChatResult,
    PromptTokensDetails,
    ProviderRequestError,
    ServerStamp,
    ToolCallArgumentsError,
    Usage,
)
from app.api import routes_agent
from app.config import get_settings
from app.main import app
from app.tools import registry, runtime
from tests.scripted_provider import ScriptedProvider
from tests.scripted_provider import text_turn as _text
from tests.scripted_provider import tool_calls_turn as _calls

READER = Path(__file__).resolve().parents[1] / "scripts" / "read_trace.py"

RECORD_KEYS = {
    "schema",
    "kind",
    "ts",
    "started",
    "run_id",
    "conversation_id",
    "actor",
    "utterance",
    "history",
    "stop_reason",
    "limit",
    "error",
    "reply",
    "summarised",
    "iterations",
    "corrections",
    "tools",
    "model_calls",
    "unmetered_calls",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "model_ms",
    "total_ms",
    "calls",
    "limits",
    "serving",
}


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


@pytest.fixture
def trace_path(tmp_path: Path) -> Path:
    return tmp_path / "traces" / "agent.jsonl"


class _ModelProvider(ScriptedProvider):
    """The scripted provider plus the model alias the real one exposes."""

    model = "gemma-4-12b"


def _metered(turn: ChatResult, prompt: int = 100, completion: int = 10) -> ChatResult:
    return turn.model_copy(
        update={
            "usage": Usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                prompt_tokens_details=PromptTokensDetails(cached_tokens=prompt // 2),
            ),
            "server": ServerStamp(model="gemma-weights", fingerprint="b1", server_ms=40),
        }
    )


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _only_record(path: Path) -> dict[str, Any]:
    records = _records(path)
    assert len(records) == 1
    assert set(records[0]) == RECORD_KEYS
    return records[0]


def _loop(provider: ScriptedProvider, path: Path, **options: Any) -> AgentLoop:
    return AgentLoop(provider, tracer=run_trace.JsonlTracer(path), **options)


# --- Happy path ---------------------------------------------------------------


def test_a_traced_run_writes_one_well_formed_record(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    history = [
        {"role": "user", "content": "earlier ask"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    provider = _ModelProvider(
        [
            _metered(_calls(("create_task", {"data": {"title": "Alpha"}}))),
            _metered(_text("Made Alpha."), prompt=150, completion=5),
        ]
    )
    with structlog.contextvars.bound_contextvars(conversation_id=7):
        result = _loop(provider, trace_path).run("Make Alpha.", history=history)

    record = _only_record(trace_path)
    assert result.run_id and record["run_id"] == result.run_id
    assert record["schema"] == run_trace.TRACE_SCHEMA == 1
    assert record["kind"] == "run"
    assert record["conversation_id"] == 7
    assert record["actor"] == "agent:loop"
    assert record["utterance"] == "Make Alpha."
    assert record["history"] == {"given": 2, "kept": 2, "dropped": 0, "messages": history}
    assert record["stop_reason"] == "completed"
    assert record["limit"] is None
    assert record["error"] == ""
    assert record["reply"] == "Made Alpha."
    assert record["summarised"] is False
    assert record["iterations"] == 2

    [tool] = record["tools"]
    assert tool["name"] == "create_task"
    assert tool["args"] == {"data": {"title": "Alpha"}}
    assert json.loads(tool["result"])["title"] == "Alpha"
    assert tool["error"] is None

    first, second = record["calls"]
    assert first["seq"] == 0 and first["phase"] == "turn" and first["status"] == "ok"
    assert first["tool_calls"] == 1 and first["finish_reason"] == "tool_calls"
    assert (first["prompt_tokens"], first["completion_tokens"], first["cached_tokens"]) == (
        100,
        10,
        50,
    )
    assert first["server_ms"] == 40 and first["served_model"] == "gemma-weights"
    assert isinstance(first["ms"], int) and first["ms"] >= 0
    assert second["status"] == "ok" and second["tool_calls"] == 0
    assert record["model_calls"] == 2 and record["unmetered_calls"] == 0
    assert (record["prompt_tokens"], record["completion_tokens"]) == (250, 15)
    assert record["cached_tokens"] == 125

    assert record["limits"]["max_iterations"] == 10
    assert record["limits"]["max_tool_calls"] == loop_module.DEFAULT_MAX_TOOL_CALLS
    assert record["limits"]["deadline_s"] is None
    serving = record["serving"]
    assert serving["model"] == "gemma-4-12b"
    assert serving["profile"] is None
    assert serving["system_prompt_sha256"] == SYSTEM_PROMPT_SHA256
    assert serving["summary_prompt_sha256"] == SUMMARY_PROMPT_SHA256
    specs = registry.tool_specs()
    assert serving["tool_schemas_sha256"] == run_trace.tool_schemas_sha256(specs)
    assert serving["tools_offered"] == len(specs)
    assert len(SYSTEM_PROMPT_SHA256) == 64


def test_history_the_budget_dropped_is_counted(
    tool_db: sessionmaker[Session], trace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        context_budget, "history_token_budget", lambda **_: 1
    )
    history = [{"role": "user", "content": "x" * 400}, {"role": "assistant", "content": "y"}]
    _loop(ScriptedProvider([_text("Hi.")]), trace_path).run("Hi", history=history)

    record = _only_record(trace_path)
    assert record["history"]["given"] == 2
    assert record["history"]["kept"] + record["history"]["dropped"] == 2
    assert record["history"]["dropped"] >= 1
    assert len(record["history"]["messages"]) == record["history"]["kept"]


def test_a_caller_bound_run_id_is_reused(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    with structlog.contextvars.bound_contextvars(run_id="given-run"):
        result = _loop(ScriptedProvider([_text("Hi.")]), trace_path).run("Hi")
    assert result.run_id == "given-run"
    assert _only_record(trace_path)["run_id"] == "given-run"


def test_unmetered_calls_record_null_tokens(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    _loop(ScriptedProvider([_text("Hi.")]), trace_path).run("Hi")
    record = _only_record(trace_path)
    [call] = record["calls"]
    assert call["prompt_tokens"] is None and call["completion_tokens"] is None
    assert record["unmetered_calls"] == 1 and record["prompt_tokens"] == 0
    assert record["serving"]["model"] is None


# --- Limits and early exits -----------------------------------------------------


def _alpha() -> tuple[str, dict[str, Any]]:
    return ("create_task", {"data": {"title": "Alpha"}})


def _cut_off() -> ChatResult:
    return ChatResult(content="and then", tool_calls=[], finish_reason="length", usage=None)


# Each limit stop, scripted so one write runs first; then the summary call.
_LIMIT_RUNS: dict[str, tuple[list[ChatResult], dict[str, int], str]] = {
    "max_iterations": ([_calls(_alpha())], {"max_iterations": 1}, "max_iterations"),
    "correction_limit": (
        [_calls(_alpha(), ("create_task", {"data": {"title": ""}}))],
        {"max_corrections": 0},
        "max_corrections",
    ),
    "truncated": ([_calls(_alpha()), _cut_off()], {}, "max_tokens"),
    "no_progress": (
        [_calls(_alpha()), _calls(("list_projects", {})), _calls(("list_projects", {}))],
        {},
        "no_progress",
    ),
    "tool_call_limit": (
        [_calls(_alpha(), ("create_task", {"data": {"title": "Beta"}}))],
        {"max_tool_calls": 1},
        "max_tool_calls",
    ),
}


@pytest.mark.parametrize("stop_reason", sorted(_LIMIT_RUNS))
def test_a_limit_stop_records_the_limit_and_the_summary_call(
    tool_db: sessionmaker[Session], trace_path: Path, stop_reason: str
) -> None:
    turns, options, limit = _LIMIT_RUNS[stop_reason]
    provider = ScriptedProvider([*turns, _text("Made Alpha; the rest wasn't done.")])
    result = _loop(provider, trace_path, **options).run("Make Alpha and Beta.")

    record = _only_record(trace_path)
    assert record["stop_reason"] == result.stop_reason == stop_reason
    assert record["limit"] == limit
    assert record["summarised"] is True
    assert record["reply"] == "Made Alpha; the rest wasn't done."
    assert [call["phase"] for call in record["calls"]] == ["turn"] * len(turns) + ["summary"]
    if limit != "no_progress":
        # The limit's configured value is in the record beside its name.
        expected = options.get(limit, loop_module.DEFAULT_MAX_TOKENS)
        assert record["limits"][limit] == expected
    if stop_reason == "truncated":
        assert record["calls"][-2]["status"] == "truncated"
    if stop_reason == "correction_limit":
        assert record["corrections"] == 1


def test_bad_tool_call_arguments_are_a_bad_args_call(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    bad = ToolCallArgumentsError(
        "create_task",
        "arguments are not valid JSON",
        usage=Usage(prompt_tokens=80, completion_tokens=7, total_tokens=87),
    )
    provider = ScriptedProvider([bad, _text("Sorry.")])
    _loop(provider, trace_path).run("Make a task")

    record = _only_record(trace_path)
    assert record["stop_reason"] == "completed"
    first, second = record["calls"]
    assert first["status"] == "bad_args"
    assert "not valid JSON" in first["failure"]
    assert (first["prompt_tokens"], first["completion_tokens"]) == (80, 7)
    assert second["status"] == "ok"
    assert record["corrections"] == 1


def test_a_stop_is_traced(tool_db: sessionmaker[Session], trace_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    result = _loop(ScriptedProvider([]), trace_path).run("Hi", cancel=cancel)

    record = _only_record(trace_path)
    assert record["stop_reason"] == result.stop_reason == "cancelled"
    assert record["limit"] is None
    assert record["calls"] == [] and record["reply"] is None


def test_a_provider_failure_is_traced_and_still_raised(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = ScriptedProvider(
        [_calls(_alpha()), ProviderRequestError("llama-server returned 503")]
    )
    with pytest.raises(AgentRunFailed) as exc_info:
        _loop(provider, trace_path).run("Make Alpha.")

    record = _only_record(trace_path)
    assert exc_info.value.result.run_id == record["run_id"]
    assert record["stop_reason"] == "provider_error"
    assert "503" in record["error"]
    assert [tool["name"] for tool in record["tools"]] == ["create_task"]
    assert record["calls"][-1]["status"] == "failed"
    assert "ProviderRequestError" in record["calls"][-1]["failure"]


def test_the_deadline_is_traced_as_its_limit(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    with pytest.raises(AgentRunFailed):
        _loop(ScriptedProvider([]), trace_path).run("Hi", deadline=0.0)

    record = _only_record(trace_path)
    assert record["stop_reason"] == "timed_out"
    assert record["limit"] == "deadline"


def test_a_crashed_tool_dispatch_is_traced(
    tool_db: sessionmaker[Session], trace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(registry, "call_tool", boom)
    with pytest.raises(AgentRunFailed):
        _loop(ScriptedProvider([_calls(_alpha())]), trace_path).run("Make Alpha.")

    record = _only_record(trace_path)
    assert record["stop_reason"] == "internal_error"
    assert record["tools"][0]["error"].startswith("internal error")


def test_a_crash_past_the_guards_is_still_traced(
    tool_db: sessionmaker[Session], trace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> Any:
        raise RuntimeError("registry broke")

    monkeypatch.setattr(registry, "tool_specs", boom)
    with pytest.raises(AgentRunFailed):
        _loop(ScriptedProvider([]), trace_path).run("Hi")

    record = _only_record(trace_path)
    assert record["stop_reason"] == "internal_error"
    assert record["error"] == "RuntimeError: registry broke"


# --- Never costs the run --------------------------------------------------------


class _WarningSpy:
    """Wraps the loop's logger, keeping each warning's event name. (structlog's
    ``capture_logs`` misses a logger cached on first use earlier in the run.)"""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.warnings: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def warning(self, event: str, **fields: Any) -> None:
        self.warnings.append(event)
        self._real.warning(event, **fields)


def test_an_unwritable_trace_path_does_not_fail_the_run(
    tool_db: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _WarningSpy(loop_module.logger)
    monkeypatch.setattr(loop_module, "logger", spy)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    provider = ScriptedProvider([_text("Hi.")])
    result = _loop(provider, blocker / "agent.jsonl").run("Hi")

    assert result.stop_reason == "completed" and result.reply == "Hi."
    assert spy.warnings == ["agent_trace_write_failed"]


def test_a_tracer_that_raises_does_not_fail_a_failed_run(
    tool_db: sessionmaker[Session],
) -> None:
    class Broken:
        def write(self, record: dict[str, Any]) -> None:
            raise OSError("disk full")

    provider = ScriptedProvider([ProviderRequestError("down")])
    with pytest.raises(AgentRunFailed) as exc_info:
        AgentLoop(provider, tracer=Broken()).run("Hi")
    # The run's own failure surfaces, not the tracer's.
    assert exc_info.value.result.stop_reason == "provider_error"


# --- Configuration --------------------------------------------------------------


def test_tracing_is_off_by_default(
    tool_db: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert get_settings().pcc_trace_path is None
    assert run_trace.tracer_from_settings() is None
    AgentLoop(ScriptedProvider([_text("Hi.")])).run("Hi")
    assert list(tmp_path.iterdir()) == []


def test_pcc_trace_path_turns_tracing_on(
    tool_db: sessionmaker[Session], trace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "pcc_trace_path", trace_path)
    AgentLoop(ScriptedProvider([_text("Hi.")])).run("Hi")
    assert _only_record(trace_path)["reply"] == "Hi."


def test_the_route_records_the_conversation(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = ScriptedProvider([_text("Hello.")])
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: _loop(provider, trace_path)
    conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages", json={"content": "Hi"}
    )
    assert response.status_code == 200
    record = _only_record(trace_path)
    assert record["conversation_id"] == conversation_id
    assert record["utterance"] == "Hi"


# --- Reader ---------------------------------------------------------------------


def test_the_reader_summarises_and_prints_records(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    _loop(ScriptedProvider([_calls(_alpha()), _text("Made Alpha.")]), trace_path).run(
        "Make Alpha."
    )
    _loop(ScriptedProvider([_calls(_alpha())]), trace_path, max_iterations=1).run(
        "Make Alpha again."
    )
    with trace_path.open("a") as handle:
        handle.write('{"torn": \n')
    run_id = _records_ok(trace_path)[0]["run_id"]

    def read(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(READER), str(trace_path), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    summary = read()
    lines = summary.stdout.splitlines()
    assert len(lines) == 3
    assert "completed" in lines[0] and run_id[:8] in lines[0]
    assert "max_iterations" in lines[1]
    assert lines[2].startswith("2 runs (completed 1, max_iterations 1)")
    assert "not JSON" in summary.stderr

    only_limits = read("--stop", "max_iterations").stdout.splitlines()
    assert len(only_limits) == 2 and "max_iterations" in only_limits[0]

    detail = read("--run", run_id[:8]).stdout
    assert f"run {run_id}" in detail
    assert "create_task" in detail and "Made Alpha." in detail
    assert "#0 turn" in detail

    as_json = json.loads(read("--run", run_id, "--json").stdout)
    assert as_json["run_id"] == run_id


def _records_ok(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
