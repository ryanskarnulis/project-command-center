"""Context capture: the exact bytes of every model call (#401).

The run trace (:mod:`app.ai.trace`, #400) records what a run *decided* and
what each call *cost*. It deliberately does not record what the model was
shown, and that is the question a misfire leaves open: was the context missing
something, or holding too much? With ``PCC_CONTEXT_PATH`` set, every model call
appends one JSONL line holding the request body byte for byte as it went to
llama-server, the response body byte for byte as it came back
(``reasoning_content`` and raw tool-call argument strings included), the typed
error the call raised, and the prompt string the server's chat template
rendered from the same request — the text the model actually tokenized, not
our JSON.

Capture happens at the one seam every call goes through
(:meth:`app.ai.providers.llamacpp.LlamaCppProvider._post`), so no call can be
missed or described second-hand. The provider knows the bytes but not which
run or which phase it is serving; both arrive by ``ContextVar``: the run id is
the one :meth:`app.ai.loop.AgentLoop.run` binds in structlog's context (the
key that joins a capture to its trace record), and the phase is the one
:func:`model_phase` names around the loop's call site.

The rules are the tracer's. Off unless ``PCC_CONTEXT_PATH`` is set; a capture
that fails is logged (``context_capture_failed``) and dropped — losing a
diagnostic line never costs the run. Nothing is reformatted, trimmed or
redacted, and thought blocks are only *recorded*: history is still built from
``ChatResult``, which never carries them.

It is a debugging tool, not something to leave on: every call is kept twice
(the request JSON and its rendered prompt). Follow it live with
``backend/scripts/watch_context.py``; the layout is in
``docs/agent-context-capture.md``. Borrowed from chess
(``chessapp/context_capture.py``, ryanskarnulis/chess#359).
"""

from __future__ import annotations

import contextvars
import json
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

# What a line in the capture file is, and its layout's version. A reader keys
# off ``schema``, as the trace's readers do; bump it on any change they must
# branch on.
KIND_MODEL_CALL = "model_call"
CAPTURE_SCHEMA = 2
# Schema 2 (#517) adds ``agent_turn`` and ``agent_hop``: the delegate caller's
# turn and hop, as the run's trace record carries them.
# The phase of a call made outside the loop's call site (a probe, a script).
PHASE_UNKNOWN = "unknown"

# How many runs' call counters one capture remembers. A counter is only needed
# while its run can still make calls; this bounds memory, nothing waits on it.
_SEQ_MEMORY = 256

_PHASE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "pcc_model_phase", default=PHASE_UNKNOWN
)


@contextmanager
def model_phase(phase: str) -> Iterator[None]:
    """Name the phase of every model call made inside this block.

    The loop wraps each call site in it (``planner`` or ``narrator``, the run
    trace's ``CallPhase``); the provider reads it back when it captures the
    call. A ``ContextVar`` rather than a ``chat`` argument, so the
    ``ChatProvider`` protocol and every test double stay as they are.
    """
    token = _PHASE.set(phase)
    try:
        yield
    finally:
        _PHASE.reset(token)


def current_phase() -> str:
    """The phase :func:`model_phase` named for this context, or ``unknown``."""
    return _PHASE.get()


def current_run_id() -> str | None:
    """The run id :meth:`AgentLoop.run` bound in structlog's context, or
    ``None`` for a call made outside a run."""
    run_id = structlog.contextvars.get_contextvars().get("run_id")
    return None if run_id is None else str(run_id)


def current_agent_ids() -> tuple[str | None, str | None]:
    """The delegate caller's turn and hop the agent route bound (#517), or
    ``None`` for each one it didn't send."""
    bound = structlog.contextvars.get_contextvars()
    turn, hop = bound.get("agent_turn"), bound.get("agent_hop")
    return (None if turn is None else str(turn), None if hop is None else str(hop))


@dataclass(frozen=True)
class CallStamp:
    """Who a call belongs to, fixed the moment it was sent.

    ``seq`` counts the calls its run *sent*, from 0 — the loop makes one
    provider call per trace ``calls`` entry, so ``(run_id, seq)`` names the
    same call as the trace record's ``calls[seq]``. ``run_id`` is ``None`` for
    a call made outside a run.
    """

    run_id: str | None
    phase: str
    seq: int
    started_at: str
    # The delegate caller's turn and hop (#517), ``None`` when not sent.
    agent_turn: str | None = None
    agent_hop: str | None = None


class ContextCapture(Protocol):
    """Whatever keeps the captured calls. Free to fail: the provider swallows it."""

    def begin(self) -> CallStamp: ...

    def record(self, stamp: CallStamp, call: dict[str, Any]) -> None: ...


# One lock for every capture in the process: the route builds a provider (and
# so a capture) per request, runs on different conversations finish together
# in worker threads, and two appends must never interleave inside one line.
_WRITE_LOCK = threading.Lock()


class JsonlContextCapture:
    """Appends one JSONL line per model call, newest last."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._seq_lock = threading.Lock()
        self._seq: OrderedDict[str | None, int] = OrderedDict()

    def begin(self) -> CallStamp:
        run_id = current_run_id()
        with self._seq_lock:
            seq = self._seq.pop(run_id, -1) + 1
            self._seq[run_id] = seq
            while len(self._seq) > _SEQ_MEMORY:
                self._seq.popitem(last=False)
        turn, hop = current_agent_ids()
        return CallStamp(
            run_id=run_id,
            phase=current_phase(),
            seq=seq,
            started_at=_now(),
            agent_turn=turn,
            agent_hop=hop,
        )

    def record(self, stamp: CallStamp, call: dict[str, Any]) -> None:
        line = json.dumps(
            {
                "schema": CAPTURE_SCHEMA,
                "kind": KIND_MODEL_CALL,
                "run_id": stamp.run_id,
                "agent_turn": stamp.agent_turn,
                "agent_hop": stamp.agent_hop,
                "phase": _answered_phase(stamp.phase, call.get("response")),
                "seq": stamp.seq,
                "started_at": stamp.started_at,
                "ended_at": _now(),
                **call,
            },
            ensure_ascii=False,
        )
        with _WRITE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def context_capture_from_settings() -> ContextCapture | None:
    """The configured capture: a :class:`JsonlContextCapture` at
    ``PCC_CONTEXT_PATH``, or ``None`` (capture off — the default)."""
    path = get_settings().pcc_context_path
    return None if path is None else JsonlContextCapture(path)


def _answered_phase(phase: str, response: Any) -> str:
    """The phase as the trace names it once the call answered (#517): a
    planner call that came back with text and no tool calls is its closing
    turn. Anything that can't be read keeps the phase it was sent with."""
    if phase != "planner" or not isinstance(response, str):
        return phase
    try:
        message = json.loads(response)["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return phase
    return phase if not isinstance(message, dict) or message.get("tool_calls") else "closing"


def _now() -> str:
    return datetime.now(UTC).isoformat()
