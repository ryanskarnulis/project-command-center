"""The run trace: one JSONL record per agent run (#400).

A misbehaving run used to leave structlog lines and the saved tool trajectory
and nothing else: no per-call cost, no record of which prompt and tool schemas
produced it, and not enough to rebuild it as an eval scenario. With
``PCC_TRACE_PATH`` set, the loop appends one record per run holding exactly
that — the utterance and the history it was shown, every tool call with its
arguments and answer, the stop reason and the limit behind it, the reply, one
entry per model call (status, wall clock, tokens), and hashes of the system
prompt and tool schemas beside the model — so a misfire is a ready-made eval
scenario and two baselines' configurations can be told apart.

Tracing is diagnostics and nothing more. It is off unless a path is
configured, and a write that fails is logged and swallowed by the loop
(:meth:`app.ai.loop.AgentLoop._write_trace`): losing a record never costs the
run. Local disk only — no service, no dependency beyond ``json``.

Borrowed from chess (``chessapp/trace.py``, ``ModelCall`` in ``brain.py``).
Read records back with ``backend/scripts/read_trace.py``; the layout is
documented in ``docs/agent-tracing.md``.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.config import get_settings

if TYPE_CHECKING:
    from app.ai.loop import AgentRunResult
    from app.ai.providers.llamacpp import ServerStamp, ToolSpec, Usage

# The record layout's version. A reader keys off it rather than off which
# fields happen to be present; bump it on any change a reader must branch on.
TRACE_SCHEMA = 2
# Schema 2 (#412) adds ``state_block`` (the text the run's user turn opened
# with) and ``lookup_turns`` (model turns of only reads before the first turn
# asking for anything else). Schema 1 records have neither; a reader rebuilds
# ``lookup_turns`` from ``calls`` and ``tools``.
# What a record in the trace file is: one agent run. On every record so a
# later kind (#401's context captures, a serving manifest) is never mistaken
# for one.
KIND_RUN = "run"

# How one model call ended. ``ok`` — a usable answer; ``truncated`` — the
# ``max_tokens`` cap cut it off (``finish_reason == "length"``); ``bad_args`` —
# the server answered but a tool call's arguments were not a JSON object
# (``ToolCallArgumentsError``); ``failed`` — no usable answer (the request
# failed, the body failed validation, or anything else raised).
CallStatus = Literal["ok", "truncated", "bad_args", "failed"]
# Which part of the run made the call: a loop turn, or the tool-free summary a
# limit stop makes (#399).
CallPhase = Literal["turn", "summary"]


def new_run_id() -> str:
    """A fresh id for one run: what the record, the run's log lines and #401's
    byte capture are keyed by. Full uuid4 hex — unique across processes."""
    return uuid.uuid4().hex


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_schemas_sha256(specs: Sequence[ToolSpec]) -> str:
    """One hash over every offered tool's wire schema, canonicalised (sorted
    keys) so it changes with the schemas, never with dict ordering."""
    wire = [spec.to_wire() for spec in specs]
    return sha256(json.dumps(wire, sort_keys=True, separators=(",", ":")))


@dataclass
class ModelCall:
    """One model round trip, as the trace records it.

    ``prompt_tokens``/``completion_tokens``/``cached_tokens`` are ``None`` when
    unknown — the call raised before the server answered, or it sent no usage
    — never a zero that reads as measured. ``ms`` is the loop's wall clock
    around the call; ``server_ms`` the server's own prompt plus generation
    time, when it said. ``failure`` names the exception of a ``failed`` or
    ``bad_args`` call.
    """

    phase: CallPhase
    status: CallStatus = "ok"
    ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    server_ms: int | None = None
    served_model: str | None = None
    fingerprint: str | None = None
    finish_reason: str | None = None
    tool_calls: int = 0
    failure: str = ""

    @property
    def metered(self) -> bool:
        return self.prompt_tokens is not None and self.completion_tokens is not None

    def meter(self, usage: Usage | None, server: ServerStamp | None) -> None:
        """Copy what the server reported about this call onto it."""
        if usage is not None:
            self.prompt_tokens = usage.prompt_tokens
            self.completion_tokens = usage.completion_tokens
            self.cached_tokens = usage.cached_tokens
        if server is not None:
            self.server_ms = server.server_ms
            self.served_model = server.model
            self.fingerprint = server.fingerprint

    def as_trace(self, seq: int) -> dict[str, Any]:
        return {
            "seq": seq,
            "phase": self.phase,
            "status": self.status,
            "ms": self.ms,
            "server_ms": self.server_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "finish_reason": self.finish_reason,
            "tool_calls": self.tool_calls,
            "served_model": self.served_model,
            "fingerprint": self.fingerprint,
            "failure": self.failure,
        }


@dataclass
class RunTrace:
    """What one run learned as it went, filled in by the loop.

    Always kept (it is a few lists), whether or not a tracer is configured;
    :meth:`record` turns it and the run's result into the JSONL record.
    Everything set before a failure is kept, which is the point: the record a
    reviewer most wants is the one a half-finished run used to leave nothing of.
    """

    run_id: str
    actor: str
    utterance: str
    conversation_id: int | None = None
    model: str | None = None
    profile: str | None = None
    history_given: int = 0
    history_kept: list[dict[str, Any]] = field(default_factory=list)
    system_prompt_sha256: str | None = None
    summary_prompt_sha256: str | None = None
    tool_schemas_sha256: str | None = None
    tools_offered: int = 0
    limits: dict[str, Any] = field(default_factory=dict)
    calls: list[ModelCall] = field(default_factory=list)
    summarised: bool = False
    corrections: int = 0
    state_block: str | None = None
    lookup_turns: int = 0
    # Set at the first turn that asked for more than reads; ends the count.
    wrote: bool = False
    started: datetime = field(default_factory=lambda: datetime.now(UTC))

    def record(
        self,
        result: AgentRunResult | None,
        *,
        limit: str | None,
        error: str = "",
        total_ms: int,
    ) -> dict[str, Any]:
        """The run as one flat record. ``result`` is ``None`` only when the run
        crashed past the loop's per-call guards (``stop_reason`` is then
        ``internal_error`` and the trajectory is whatever the trace saw)."""
        calls = self.calls
        tools = (
            [
                {
                    "name": record.tool,
                    "args": record.arguments,
                    "result": record.result,
                    "error": record.error,
                }
                for record in result.tool_calls
            ]
            if result is not None
            else []
        )
        return {
            "schema": TRACE_SCHEMA,
            "kind": KIND_RUN,
            "ts": datetime.now(UTC).isoformat(),
            "started": self.started.isoformat(),
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "actor": self.actor,
            "utterance": self.utterance,
            "state_block": self.state_block,
            "lookup_turns": self.lookup_turns,
            "history": {
                "given": self.history_given,
                "kept": len(self.history_kept),
                "dropped": self.history_given - len(self.history_kept),
                "messages": self.history_kept,
            },
            "stop_reason": result.stop_reason if result is not None else "internal_error",
            "limit": limit,
            "error": error,
            "reply": result.reply if result is not None else None,
            "summarised": self.summarised,
            "iterations": result.iterations if result is not None else 0,
            "corrections": self.corrections,
            "tools": tools,
            "model_calls": len(calls),
            "unmetered_calls": sum(1 for call in calls if not call.metered),
            "prompt_tokens": sum(call.prompt_tokens or 0 for call in calls),
            "completion_tokens": sum(call.completion_tokens or 0 for call in calls),
            "cached_tokens": sum(call.cached_tokens or 0 for call in calls),
            "model_ms": sum(call.ms for call in calls),
            "total_ms": total_ms,
            "calls": [call.as_trace(seq) for seq, call in enumerate(calls)],
            "limits": self.limits,
            "serving": {
                "model": self.model,
                # The model profile the requests were built from (#402):
                # its file's name, ``default`` for a model with no file, null
                # for a provider that carries none (a scripted one).
                "profile": self.profile,
                "system_prompt_sha256": self.system_prompt_sha256,
                "summary_prompt_sha256": self.summary_prompt_sha256,
                "tool_schemas_sha256": self.tool_schemas_sha256,
                "tools_offered": self.tools_offered,
            },
        }


class Tracer(Protocol):
    """Whatever records a finished run. Free to fail: the loop swallows it."""

    def write(self, record: dict[str, Any]) -> None: ...


# One lock for every JsonlTracer in the process: runs on different
# conversations finish concurrently in worker threads, and two appends must
# never interleave inside one line.
_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class JsonlTracer:
    """Appends each run to a JSONL file, newest last, one line per run.

    JSONL because runs are appended forever and read back a few at a time: a
    crash mid-write costs at most the last line, ``tail -f`` works during a
    live session, and a file of runs loads with a one-line comprehension.
    """

    path: Path

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str, ensure_ascii=False)
        with _WRITE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def tracer_from_settings() -> Tracer | None:
    """The configured tracer: a :class:`JsonlTracer` at ``PCC_TRACE_PATH``, or
    ``None`` (tracing off — the default)."""
    path = get_settings().pcc_trace_path
    return None if path is None else JsonlTracer(path)
