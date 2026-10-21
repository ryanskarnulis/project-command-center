"""Context capture at the provider seam (#401), against a faked llama-swap.

``httpx.MockTransport`` plays llama-swap: ``/v1/chat/completions`` answers
with a canned body and ``/upstream/<model>/apply-template`` with a rendered
prompt. The point of the capture is that it is exact, so the assertions
compare bytes: what the transport received is what the record holds, and what
the transport served is what the record holds. The rest — run id, phase, seq —
is the bookkeeping that joins a capture to its run trace record (#400). And a
capture that fails, however it fails, never costs the call.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import context_capture as capture_module
from app.ai import trace as run_trace
from app.ai.context_capture import (
    CAPTURE_SCHEMA,
    KIND_MODEL_CALL,
    PHASE_UNKNOWN,
    CallStamp,
    JsonlContextCapture,
    context_capture_from_settings,
    model_phase,
)
from app.ai.loop import AgentLoop
from app.ai.providers import llamacpp as provider_module
from app.ai.providers.llamacpp import (
    LlamaCppProvider,
    ProviderRequestError,
    ToolCallArgumentsError,
    provider_from_settings,
)
from app.config import get_settings
from app.tools import runtime

WATCHER = Path(__file__).resolve().parents[1] / "scripts" / "watch_context.py"

_USER = [{"role": "user", "content": "make «Alpha» — ☕"}]
# A response the way llama-server writes it: its own spacing, a thought block,
# and the reply. The capture keeps it as sent.
_TEXT_RESPONSE = (
    b'{"choices":[{"index":0,"finish_reason":"stop","message":'
    b'{"role":"assistant","content":"Done.",'
    b'"reasoning_content":"The user wants Alpha \\u2014 fine."}}],'
    b' "model":"gemma-4-12b",'
    b' "usage":{"prompt_tokens":12,"completion_tokens":3,"total_tokens":15}}'
)
_BAD_ARGS_RESPONSE = (
    b'{"choices":[{"index":0,"finish_reason":"tool_calls","message":'
    b'{"role":"assistant","content":null,"tool_calls":[{"id":"c0","type":"function",'
    b'"function":{"name":"create_task","arguments":"{not json"}}]}}]}'
)
_PROMPT = "<|turn>user\nmake «Alpha» — ☕<turn|>\n<|turn>model\n"


class FakeSwap:
    """A llama-swap stand-in that remembers every request it received."""

    def __init__(
        self,
        *,
        chat: httpx.Response | Exception | None = None,
        template: httpx.Response | Exception | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self._chat = chat or httpx.Response(200, content=_TEXT_RESPONSE)
        self._template = template or httpx.Response(200, json={"prompt": _PROMPT})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self._chat if request.url.path.endswith("/chat/completions") else self._template
        if isinstance(answer, Exception):
            raise answer
        return answer

    def provider(self, capture: Any) -> LlamaCppProvider:
        client = httpx.Client(transport=httpx.MockTransport(self))
        return LlamaCppProvider(
            "http://swap.test:8200/v1", "gemma-4-12b", client=client, capture=capture
        )

    def chat_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/chat/completions")]


@pytest.fixture
def context_path(tmp_path: Path) -> Path:
    return tmp_path / "traces" / "context.jsonl"


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- Happy path ---------------------------------------------------------------


def test_a_call_is_captured_byte_for_byte_with_its_rendered_prompt(
    context_path: Path,
) -> None:
    swap = FakeSwap()
    provider = swap.provider(JsonlContextCapture(context_path))

    with structlog.contextvars.bound_contextvars(run_id="run-1"), model_phase("turn"):
        first = provider.chat(_USER, max_tokens=64)
        provider.chat(_USER, max_tokens=64)

    assert first.content == "Done."  # the run gets its answer, thought block excluded
    chat_request = swap.chat_requests()[0]
    template_request = swap.requests[1]
    # The rendered prompt is asked of llama-swap's upstream route, with the
    # very bytes the completion was sent.
    assert str(template_request.url) == (
        "http://swap.test:8200/upstream/gemma-4-12b/apply-template"
    )
    assert template_request.content == chat_request.content

    records = _lines(context_path)
    assert len(records) == 2
    record = records[0]
    assert record["schema"] == CAPTURE_SCHEMA and record["kind"] == KIND_MODEL_CALL
    assert (record["run_id"], record["phase"], record["seq"]) == ("run-1", "turn", 0)
    assert records[1]["seq"] == 1
    assert record["url"] == "http://swap.test:8200/v1/chat/completions"
    assert record["request"].encode("utf-8") == chat_request.content
    assert record["response"].encode("utf-8") == _TEXT_RESPONSE
    assert "reasoning_content" in record["response"]
    assert record["status_code"] == 200
    assert record["error"] == ""
    assert record["template"] == {"prompt": _PROMPT}
    assert isinstance(record["ms"], int)
    assert record["started_at"] <= record["ended_at"]


def test_capture_does_not_change_the_request_bytes(context_path: Path) -> None:
    plain, captured = FakeSwap(), FakeSwap()
    plain.provider(None).chat(_USER, max_tokens=64, timeout=30.0)
    captured.provider(JsonlContextCapture(context_path)).chat(_USER, max_tokens=64, timeout=30.0)

    # Off: one request per call. On: the same bytes, plus the template ask.
    assert len(plain.requests) == 1
    assert len(captured.chat_requests()) == 1
    assert plain.requests[0].content == captured.chat_requests()[0].content
    assert plain.requests[0].headers == captured.chat_requests()[0].headers
    assert (
        plain.requests[0].extensions["timeout"]
        == (captured.chat_requests()[0].extensions["timeout"])
    )


def test_a_call_outside_a_run_is_captured_as_such(context_path: Path) -> None:
    FakeSwap().provider(JsonlContextCapture(context_path)).chat(_USER)
    record = _lines(context_path)[0]
    assert record["run_id"] is None and record["phase"] == PHASE_UNKNOWN


def test_a_loop_run_and_its_captures_share_the_run_id(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch, context_path: Path, tmp_path: Path
) -> None:
    factory: sessionmaker[Session] = sessionmaker(bind=test_engine)
    monkeypatch.setattr(runtime, "session_factory", factory)
    trace_path = tmp_path / "traces" / "agent.jsonl"
    provider = FakeSwap().provider(JsonlContextCapture(context_path))
    loop = AgentLoop(provider, tracer=run_trace.JsonlTracer(trace_path))

    result = loop.run("Make Alpha.")

    run = _lines(trace_path)[0]
    captures = _lines(context_path)
    assert result.reply == "Done."
    assert [c["run_id"] for c in captures] == [run["run_id"]] == [result.run_id]
    # One capture per trace ``calls`` entry, joined on (run_id, seq) and phase.
    assert [(c["seq"], c["phase"]) for c in captures] == [
        (call["seq"], call["phase"]) for call in run["calls"]
    ]


# --- What a failed call records ---------------------------------------------------


def test_an_unanswered_call_records_its_error_and_still_raises(context_path: Path) -> None:
    swap = FakeSwap(chat=httpx.ConnectError("refused"))
    with pytest.raises(ProviderRequestError):
        swap.provider(JsonlContextCapture(context_path)).chat(_USER)

    record = _lines(context_path)[0]
    assert record["error"].startswith("ProviderRequestError: llama-server request failed")
    assert record["request"].encode("utf-8") == swap.chat_requests()[0].content
    assert record["status_code"] is None and record["response"] is None
    # A server that never answered is not asked to render a template.
    assert record["template"] is None
    assert len(swap.requests) == 1


def test_bad_tool_call_arguments_record_the_error_beside_the_bytes(
    context_path: Path,
) -> None:
    swap = FakeSwap(chat=httpx.Response(200, content=_BAD_ARGS_RESPONSE))
    with pytest.raises(ToolCallArgumentsError):
        swap.provider(JsonlContextCapture(context_path)).chat(_USER)

    record = _lines(context_path)[0]
    assert record["error"].startswith("ToolCallArgumentsError:")
    assert record["response"].encode("utf-8") == _BAD_ARGS_RESPONSE
    assert record["template"] == {"prompt": _PROMPT}


def test_a_failed_template_render_is_recorded_not_raised(context_path: Path) -> None:
    for template, expected in (
        (httpx.Response(404, text="no route"), "HTTP 404: no route"),
        (httpx.Response(200, json={"nope": 1}), "apply-template answered without a prompt"),
        (httpx.ReadTimeout("slow"), "ReadTimeout: slow"),
    ):
        context_path.unlink(missing_ok=True)
        result = FakeSwap(template=template).provider(JsonlContextCapture(context_path)).chat(_USER)
        assert result.content == "Done."
        assert _lines(context_path)[0]["template"]["error"].startswith(expected)


# --- Never costs the run ----------------------------------------------------------


class _WarningSpy:
    """Wraps the provider's logger, keeping each warning's event name."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.warnings: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def warning(self, event: str, **fields: Any) -> None:
        self.warnings.append(event)
        self._real.warning(event, **fields)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _WarningSpy:
    spy = _WarningSpy(provider_module.logger)
    monkeypatch.setattr(provider_module, "logger", spy)
    return spy


def test_an_unwritable_capture_path_does_not_fail_the_call(
    tmp_path: Path, spy: _WarningSpy
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    result = FakeSwap().provider(JsonlContextCapture(blocker / "context.jsonl")).chat(_USER)
    assert result.content == "Done."
    assert spy.warnings == ["context_capture_failed"]


def test_a_capture_that_raises_never_replaces_the_calls_outcome(spy: _WarningSpy) -> None:
    class Broken:
        def __init__(self, *, begin: bool) -> None:
            self._begin = begin

        def begin(self) -> CallStamp:
            if self._begin:
                raise RuntimeError("begin broke")
            return CallStamp(run_id=None, phase="turn", seq=0, started_at="")

        def record(self, stamp: CallStamp, call: dict[str, Any]) -> None:
            raise OSError("disk full")

    for broken in (Broken(begin=True), Broken(begin=False)):
        assert FakeSwap().provider(broken).chat(_USER).content == "Done."
        # A failing call keeps its own typed error, not the capture's.
        with pytest.raises(ProviderRequestError):
            FakeSwap(chat=httpx.Response(503, text="loading")).provider(broken).chat(_USER)
    assert spy.warnings == ["context_capture_failed"] * 4


# --- Configuration ------------------------------------------------------------------


def test_capture_is_off_by_default() -> None:
    assert get_settings().pcc_context_path is None
    assert context_capture_from_settings() is None
    with provider_from_settings() as provider:
        assert provider._capture is None


def test_pcc_context_path_turns_capture_on(
    monkeypatch: pytest.MonkeyPatch, context_path: Path
) -> None:
    monkeypatch.setattr(get_settings(), "pcc_context_path", context_path)
    capture = context_capture_from_settings()
    assert isinstance(capture, JsonlContextCapture) and capture.path == context_path
    with provider_from_settings() as provider:
        assert isinstance(provider._capture, JsonlContextCapture)


def test_the_seq_counters_are_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(capture_module, "_SEQ_MEMORY", 2)
    capture = JsonlContextCapture(tmp_path / "c.jsonl")
    for run_id in ("a", "b", "c"):
        with structlog.contextvars.bound_contextvars(run_id=run_id):
            assert capture.begin().seq == 0
    assert list(capture._seq) == ["b", "c"]


# --- Watcher ------------------------------------------------------------------------


@pytest.fixture
def captured_run(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[tuple[Path, Path, str], None, None]:
    factory: sessionmaker[Session] = sessionmaker(bind=test_engine)
    monkeypatch.setattr(runtime, "session_factory", factory)
    context_path = tmp_path / "context.jsonl"
    trace_path = tmp_path / "agent.jsonl"
    provider = FakeSwap().provider(JsonlContextCapture(context_path))
    result = AgentLoop(provider, tracer=run_trace.JsonlTracer(trace_path)).run("Make Alpha.")
    yield context_path, trace_path, result.run_id


def test_the_watcher_prints_calls_verbatim_then_the_runs_outcome(
    captured_run: tuple[Path, Path, str],
) -> None:
    context_path, trace_path, run_id = captured_run
    with context_path.open("a", encoding="utf-8") as handle:
        handle.write('{"torn": \n')

    watched = subprocess.run(
        [
            sys.executable,
            str(WATCHER),
            str(context_path),
            "--trace",
            str(trace_path),
            "--from-start",
            "--once",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout

    assert f"run {run_id}" in watched
    assert "turn · seq 0" in watched and "HTTP 200" in watched
    assert _PROMPT in watched  # the rendered prompt, exactly as captured
    assert _TEXT_RESPONSE.decode("utf-8") in watched  # the raw response body
    assert "unreadable context line skipped" in watched
    # The outcome lands after the run's own calls.
    assert watched.index("── outcome ──") > watched.index("▸ response")
    assert "utterance: Make Alpha." in watched and "reply: Done." in watched


def test_the_watcher_can_show_request_json_and_filter_by_phase(
    captured_run: tuple[Path, Path, str],
) -> None:
    context_path, _, _ = captured_run
    spec = importlib.util.spec_from_file_location("watch_context", WATCHER)
    assert spec is not None and spec.loader is not None
    watch_context: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch_context)

    out = io.StringIO()
    watch_context.main([str(context_path), "--json", "--from-start", "--once"], out=out)
    request = _lines(context_path)[0]["request"]
    assert f"▸ request\n{request}\n" in out.getvalue()

    out = io.StringIO()
    watch_context.main([str(context_path), "--phase", "summary", "--from-start", "--once"], out=out)
    assert out.getvalue() == ""
