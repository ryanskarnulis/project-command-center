"""The in-app agent loop: plan → call tools → observe → respond.

Drives the local model (through the llama.cpp provider) against the shared
tool registry (``app/tools/registry.py``) — the same tool surface, Pydantic
argument validation, session/actor plumbing, and audit trail the MCP server
exposes, dispatched in-process. Every mutation is stamped ``agent:loop`` in
``activity_events`` and every delete is a restorable soft delete, so
everything the loop does is auditable and undoable.

Termination is structural: at most ``max_iterations`` provider turns, plus a
separate bounded budget of self-correction turns for schema-invalid tool
calls — arguments that aren't a JSON object (``ToolCallArgumentsError`` from
the provider), arguments that fail the tool's argument model, or a tool name
the registry doesn't know. Domain rejections from the service layer ("task 7
is blocked", "not found") are not corrections: they are fed back as ordinary
tool results for the model to react to within the iteration budget, matching
the MCP server's behavior.

Time is bounded at both boundaries: the deadline is checked before every
provider call *and* before every tool dispatch, so no call is ever *started*
past the deadline (#108). Tool bodies are synchronous and not preemptible, so
the guarantee is "nothing starts late", not "nothing runs late": a run's true
ceiling is the deadline plus the duration of at most one already-running tool
call. Tools are in-process service-layer calls against local SQLite, so that
overshoot is small and bounded in practice; the budget should leave headroom
for it rather than being sized flush against the proxy timeout.

Cancellation (#324) uses the same two boundaries. A caller-owned
``threading.Event`` (set by ``POST /agent/conversations/{id}/cancel``) is
checked before every provider call and before every tool dispatch; once set,
the run stops there with stop reason ``cancelled`` and the tool calls that
already ran stay committed and undoable. It is cooperative, not preemptive: a
tool already running finishes, and a provider HTTP request already in flight
is allowed to complete. If that turn asked for tools, they are recorded as
not run; if it was the final text reply, the run simply completed — there was
no boundary left to stop at. So Stop takes effect within one model turn, never
mid-tool.

Every model call is capped (#397): ``max_tokens`` goes out on every request
(the model profile's cap, #402), because llama-server's own default is
unbounded and a degenerate generation would otherwise run until the run's
time budget. A call the cap cut off (``finish_reason == "length"``) never
passes its *words* on: a cut-off text turn is not a reply, so the fragment is
dropped — not returned, not appended to the transcript — and the run ends
with stop reason ``truncated`` and no reply of its own. A cut-off turn's *tool calls*
still run: the provider parses each call's arguments whole before the result
exists, so a call that arrived is a complete call. Only the text fragment
beside them is dropped from history.

Work is bounded too, not just turns (#398). A turn in which *every* call
repeats one this run already made and comes back with the same answer ends
the run with stop reason ``no_progress``: nothing new can come back from
another turn, so the loop stops instead of spending the rest of the iteration
budget re-reading. The repeat is keyed on the tool, its canonicalised
arguments *and* what it answered (:func:`_exchange_key`) — a repeated call
that comes back different (a list after a write) did new work and the run
goes on. And a run dispatches at most ``max_tool_calls`` tool calls (default
:data:`DEFAULT_MAX_TOOL_CALLS`): a call past the cap is answered "not run"
(one ``role: tool`` answer per call, so the transcript stays well-formed),
never reaches the registry, and the run ends ``tool_call_limit`` after that
turn — the next one could only be refused more of the same. Both are limits
like ``max_iterations``, not failures.

A run that stops at a limit — ``max_iterations``, ``correction_limit``,
``truncated``, ``no_progress`` or ``tool_call_limit`` — still says what it did
(#399). If any call reached the service layer, the loop makes one tool-free
model call that reads a code-built record of the run (:mod:`app.ai.run_record`:
done / refused / looked up / not run, from the :class:`ToolCallRecord` list,
never from the model's words) under the note that whatever the record does not
show as done was not done, and saves its text as the reply. The stop reason is
unchanged. When nothing ran, or the summary call fails, runs out of time, is
cut off, or comes back empty, the reply stays ``None`` as before. ``cancelled``
and the failure stops never summarise: the user asked it to stop, or the run
broke.

Every run can leave a trace (#400): with ``PCC_TRACE_PATH`` set, the loop
appends one JSONL record per run (:mod:`app.ai.trace`) — the utterance, the
history kept and dropped, every tool call, the stop reason and its limit, the
reply, one entry per model call with its status, time and tokens, and hashes of
the prompts and tool schemas. It is written from :meth:`AgentLoop.run`, so
every exit — completed, a limit, a Stop, a failure, a crash — leaves one, and a
write that fails is logged and never costs the run. Each run has a ``run_id``
(bound on its log lines, returned on the result) that the record is keyed by.
With ``PCC_CONTEXT_PATH`` set, the provider also captures every model call's
exact bytes keyed by that ``run_id`` (:mod:`app.ai.context_capture`, #401).

Every failure the loop feeds back takes one shape (#413): the ``role: tool``
answer is ``{"ok": false, "error": …, "retry": "different_args" | "never",
…}`` plus whatever recovery needs (:mod:`app.tools.refusals`) — a schema
error, an unknown tool, a domain refusal, a call never run, a crash. The
:class:`ToolCallRecord` keeps the bare error text.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel, ValidationError

from app.ai import context_budget, model_profile, run_record, state_block
from app.ai import trace as run_trace
from app.ai.context_capture import model_phase
from app.ai.providers.llamacpp import (
    ChatResult,
    ProviderError,
    ToolCall,
    ToolCallArgumentsError,
    ToolSpec,
    tool_result_message,
)
from app.tools import registry
from app.tools.refusals import RETRY_DIFFERENT_ARGS, RETRY_NEVER, Retry, ToolRefusal
from app.tools.registry import ToolError, UnknownToolError

logger = structlog.get_logger(__name__)

# Stamped into activity_events.actor for every write the loop makes. NULL is
# the user, "agent:mcp" an external MCP client, this the in-app loop.
LOOP_ACTOR = "agent:loop"

# Actors a trusted delegate caller may bind via the ``X-Agent-Actor`` header
# in place of ``LOOP_ACTOR`` (agent-standard/delegate-api.md). Unrecognized
# values fall back to the default rather than erroring, so a caller can never
# stamp an arbitrary identity into the audit trail.
DELEGATE_ACTORS = frozenset({"agent:conductor"})

# Recorded (and fed back) for a tool call the run's deadline passed before we
# could start it — the call never reached the registry or the service layer.
_SKIPPED_ERROR = (
    f"{run_record.NOT_RUN_PREFIX}the agent run's time budget was exhausted before this call"
)

# Recorded (and fed back) for a tool call the user's Stop arrived before — the
# call never reached the registry or the service layer.
_CANCELLED_ERROR = f"{run_record.NOT_RUN_PREFIX}the user stopped the agent run before this call"

# Recorded (and fed back) for a call past the run's tool-call cap (#398) — the
# call never reached the registry or the service layer.
_TOOL_CALL_LIMIT_ERROR = (
    f"{run_record.NOT_RUN_PREFIX}the agent run reached its limit of tool calls before this call"
)


def resolve_actor(header_value: str | None) -> str:
    """The audit actor for a run given an ``X-Agent-Actor`` header, if any.

    Returns the header value only when it names a recognized delegate actor
    (``agent:conductor``); an absent or unrecognized header falls back to the
    default :data:`LOOP_ACTOR`.
    """
    if header_value is not None and header_value in DELEGATE_ACTORS:
        return header_value
    return LOOP_ACTOR

# The system prompt is composed in layers (agent-standard/STANDARD.md §5):
#   1. app base prompt — PCC's behavioral contract and tool guidance (below);
#   2. global Glitch — the vendored house personality (verbatim canonical
#      text, see personality-global.md); PCC adds no app flavor on top;
#   3. no dynamic layer: the system prompt is byte-identical across runs and
#      days, so the server's prompt cache keeps it with the tool schemas. What
#      changes per run — today, the calendar, the projects and today's counts
#      — is the state block at the head of the current user message (#412,
#      ``app/ai/state_block.py``), never saved into history.
# The vendored Glitch body is never edited here: fix drift by re-copying from
# agent-standard/ (../agent-standard/check-sync.sh).

# Layer 1 — app base prompt (app-owned behavioral contract). Upholds the
# standard's invariants: tools-only action, no invented results, deterministic
# truth, clarify-on-ambiguity.
_APP_BASE_PROMPT = """\
You are the assistant for Project Command Center (PCC), the user's project and \
task manager. You act only by calling the provided tools.

Rules:
- The user's message opens with a state block the app wrote at the start of this run: \
today's date and calendar, the open projects with their ids, and today's task counts. Its \
project ids are current; find any other id with the list_*/get_*/search tools; never guess \
an id. Take dates (today, tomorrow, a weekday, the end of the month) from its calendar.
- Prefer the specific tool: complete_task to finish a task, update_task to edit its fields.
- Every delete is a soft delete into a restorable trash; there is no permanent delete.
- A rejected tool call comes back with "retry". On "different_args", fix the arguments \
and call again. On "never", the same call cannot succeed: do not repeat it; follow \
"next" and the ids the rejection lists, or tell the user what stands in the way.
- When the work is done (or turns out to be impossible), reply with plain text: a short \
summary of what you did or found. State only what the tool results confirm — never invent \
an id, a task, or an outcome the tools didn't return."""

# Layer 2 — global Glitch, the vendored house personality (STANDARD.md §5).
_PERSONALITY_PATH = Path(__file__).with_name("personality-global.md")


def _load_global_personality() -> str:
    """The vendored Glitch text, minus its one leading ``<!-- vendored -->`` line.

    Read once at import from the copy shipped alongside this module (present in
    the editable-installed source tree at runtime). The body is canonical and
    must never be edited in place — re-vendor to change Glitch.
    """
    lines = _PERSONALITY_PATH.read_text(encoding="utf-8").splitlines()
    body = [line for line in lines if not line.startswith("<!-- vendored")]
    return "\n".join(body).strip()


_GLOBAL_PERSONALITY = _load_global_personality()


# The layered system prompt: app base → global Glitch. PCC ships no app-flavor
# layer (nothing has earned it) and no dynamic one (#412). The run trace hashes
# it (#400), so its hash changes with its wording and never with the calendar.
SYSTEM_PROMPT = f"{_APP_BASE_PROMPT}\n\n{_GLOBAL_PERSONALITY}"
SYSTEM_PROMPT_SHA256 = run_trace.sha256(SYSTEM_PROMPT)
# The limit summary's prompt (#399), hashed the same way.
SUMMARY_PROMPT_SHA256 = run_trace.sha256(
    f"{run_record.SUMMARY_RULES}\n\n{_GLOBAL_PERSONALITY}"
)

# The per-call completion cap (#397) for a provider with no model profile (a
# scripted one). It is the completion reserve the context budget already sets
# aside in the window, so a capped call always fits. A real provider carries
# its model's profile (#402), whose caps the loop runs at instead.
DEFAULT_MAX_TOKENS = model_profile.DEFAULT_TURN_MAX_TOKENS

# The per-run tool-call cap (#398). The largest deployed run made 18 calls (an
# 8-task batch, made twice after schema errors), so a real batch fits with
# headroom; a run that needs more is better split across messages than left
# to spin. Every call that reaches the registry counts, schema-invalid ones
# included — that is how the 18 was counted.
DEFAULT_MAX_TOOL_CALLS = 24

# The summary call's completion cap (#399): a few sentences of what the run
# did. Far under the turn cap, so a runaway summary is cut off — and dropped —
# long before it can eat the run's time budget. As above, the profile's own
# summary cap wins when the provider has one.
SUMMARY_MAX_TOKENS = model_profile.DEFAULT_SUMMARY_MAX_TOKENS

# ``finish_reason`` of a call the ``max_tokens`` cap cut off.
_FINISH_LENGTH = "length"

_StopReason = Literal[
    "completed",
    "max_iterations",
    "truncated",
    "no_progress",
    "tool_call_limit",
    "correction_limit",
    "provider_error",
    "timed_out",
    "internal_error",
    "cancelled",
]

# HTTP status the route surfaces for each failure stop reason: a provider fault
# is an upstream 502; exhausting the time budget is a 504; an unexpected
# exception (a bug, a DB failure) is our own 500. ``cancelled`` is absent on
# purpose: a user-requested stop is not a failure. Neither are ``truncated``
# (the model ran on past the per-call token cap), ``no_progress`` (a turn that
# only repeated exchanges the run had already seen) or ``tool_call_limit``
# (the per-run tool-call cap): each is a limit like ``max_iterations``, not a
# fault. All of them finish like
# ``max_iterations`` — a 200 carrying the truthful partial turn — so the client
# renders what ran from the response rather than treating Stop as an error.
_FAILURE_STATUS: dict[_StopReason, int] = {
    "provider_error": 502,
    "timed_out": 504,
    "internal_error": 500,
}

# Which configured limit each limit stop ran into, as the trace names it
# (#400): the key in its ``limits`` block, or the rule's name for a stop with
# no number behind it. Stops absent here (completed, cancelled, failures other
# than the deadline) hit no limit.
_STOP_LIMITS: dict[str, str] = {
    "max_iterations": "max_iterations",
    "correction_limit": "max_corrections",
    "truncated": "max_tokens",
    "tool_call_limit": "max_tool_calls",
    "no_progress": "no_progress",
    "timed_out": "deadline",
}

# Recorded (and fed back) for a call whose dispatch blew up unexpectedly. The
# call may or may not have reached the service layer, so the record claims
# neither success nor a clean rejection (#103).
_INTERNAL_ERROR = "internal error: the tool call failed unexpectedly"

# Recorded (and fed back) for the calls after one that crashed — the run ends
# there, so they never reached the registry or the service layer (#455).
_CRASHED_BATCH_ERROR = (
    f"{run_record.NOT_RUN_PREFIX}an earlier tool call in this turn failed unexpectedly"
)


class ChatProvider(Protocol):
    """What the loop needs from a provider — matched by ``LlamaCppProvider``."""

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ChatResult: ...


@dataclass(frozen=True)
class _RunContext:
    """What a limit stop needs from the run to summarise it (#399), and the
    trace the run fills in as it goes (#400)."""

    user_message: str
    deadline: float | None
    cancel: threading.Event | None
    trace: run_trace.RunTrace


class ToolCallRecord(BaseModel):
    """One dispatched tool call: what ran and what came back.

    Exactly one of ``result``/``error`` is set. This is the loop's own record
    (for the caller, and for slice-2 persistence) — the model sees the same
    text via its ``role: tool`` message.
    """

    tool: str
    arguments: dict[str, Any]
    result: str | None = None
    error: str | None = None


class AgentRunResult(BaseModel):
    """Outcome of one loop run. ``messages`` is the full transcript.

    ``run_id`` names the run: its log lines carry it and its trace record
    (#400) is keyed by it. Empty only on a result built outside ``run``.
    """

    reply: str | None
    stop_reason: _StopReason
    tool_calls: list[ToolCallRecord]
    iterations: int
    messages: list[dict[str, Any]]
    run_id: str = ""


class AgentRunFailed(Exception):
    """A run that ran but couldn't finish: the provider failed, or the time
    budget was exhausted mid-run.

    Carries the *partial* :class:`AgentRunResult` — the tool calls that did run
    before the failure — so the caller can persist a truthful record of what
    happened (see ``routes_agent.post_message``) before surfacing the error.
    ``http_status`` is what the route returns to the client (502 provider, 504
    timeout, 500 unexpected internal failure).
    """

    def __init__(
        self, result: AgentRunResult, *, http_status: int, message: str
    ) -> None:
        super().__init__(message)
        self.result = result
        self.http_status = http_status


class AgentLoop:
    """Bounded tool-calling loop over one provider and the shared registry."""

    def __init__(
        self,
        provider: ChatProvider,
        *,
        max_iterations: int = 10,
        max_corrections: int = 3,
        max_tokens: int | None = None,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        tracer: run_trace.Tracer | None = None,
    ) -> None:
        """``tracer`` receives one record per run (#400); ``None`` (the
        default) uses the configured one — ``PCC_TRACE_PATH``, or no tracing.

        The per-call caps are the provider's model profile's (#402) when it
        carries one, else :data:`DEFAULT_MAX_TOKENS` and
        :data:`SUMMARY_MAX_TOKENS`. ``max_tokens`` overrides the turn cap; the
        summary's is never above it."""
        profile = getattr(provider, "profile", None)
        self._profile = profile if isinstance(profile, model_profile.ModelProfile) else None
        if max_tokens is None:
            max_tokens = (
                self._profile.turn_max_tokens if self._profile is not None else DEFAULT_MAX_TOKENS
            )
        summary_cap = (
            self._profile.summary_max_tokens if self._profile is not None else SUMMARY_MAX_TOKENS
        )
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be >= 1")
        self._provider = provider
        self._max_iterations = max_iterations
        self._max_corrections = max_corrections
        self._max_tokens = max_tokens
        self._summary_max_tokens = min(max_tokens, summary_cap)
        self._max_tool_calls = max_tool_calls
        self._tracer = tracer if tracer is not None else run_trace.tracer_from_settings()

    def run(
        self,
        user_message: str,
        *,
        history: Sequence[dict[str, Any]] | None = None,
        actor: str = LOOP_ACTOR,
        deadline: float | None = None,
        cancel: threading.Event | None = None,
    ) -> AgentRunResult:
        """Run the loop for one user message. Always returns or raises cleanly.

        Binds a request ID for the whole run unless the caller (e.g. the HTTP
        middleware, come slice 2) already bound one — every tool call and
        provider log line of the run then carries the same ID.

        ``actor`` is stamped into ``activity_events`` for every mutation the
        run makes: the default in-app loop identity, or a delegate actor (e.g.
        ``agent:conductor``) resolved from the request's ``X-Agent-Actor``
        header by :func:`resolve_actor`.

        ``deadline`` is an absolute :func:`time.monotonic` value: once it passes
        the loop stops before the next provider call *and* before the next tool
        dispatch, and each provider call is capped at the time remaining, so
        nothing is ever started past the deadline. A synchronous tool already
        in flight is not interrupted, so a run's ceiling is the deadline plus
        at most one tool call's duration (see the module docstring).
        A provider failure or a crossed deadline raises :class:`AgentRunFailed`
        carrying the partial trajectory — the caller persists it, then surfaces
        the error. ``None`` means no time bound (the default, used by tests).

        ``cancel`` is checked at the same two boundaries as the deadline. Once
        it is set the run *returns* (not raises) with stop reason
        ``cancelled`` and the partial trajectory: the calls that ran, plus any
        calls of the current turn it declined to start, recorded as not run.
        A provider request or tool call already in flight is not interrupted.

        Every exit writes the run's trace record (#400) when a tracer is
        configured. The run is named by a ``run_id`` bound on its log lines —
        one the caller already bound is reused — and returned on the result
        (and on a failure's partial result). A ``conversation_id`` the caller
        bound is recorded with it.
        """
        bound = structlog.contextvars.get_contextvars()
        bindings: dict[str, str] = {}
        if "request_id" not in bound:
            bindings["request_id"] = uuid.uuid4().hex[:8]
        run_id = str(bound.get("run_id") or run_trace.new_run_id())
        bindings["run_id"] = run_id
        conversation_id = bound.get("conversation_id")
        trace = run_trace.RunTrace(
            run_id=run_id,
            actor=actor,
            utterance=user_message,
            conversation_id=conversation_id if isinstance(conversation_id, int) else None,
            # The alias the provider sends; the weights behind it are in each
            # call's ``served_model``; the profile the request was built from
            # (#402) beside it.
            model=getattr(self._provider, "model", None),
            profile=None if self._profile is None else self._profile.name,
            limits={
                "max_iterations": self._max_iterations,
                "max_corrections": self._max_corrections,
                "max_tokens": self._max_tokens,
                "summary_max_tokens": self._summary_max_tokens,
                "max_tool_calls": self._max_tool_calls,
                # Filled from the first turn's own clock reading (``_run``).
                "deadline_s": None,
            },
        )
        started = perf_counter()
        with structlog.contextvars.bound_contextvars(**bindings):
            try:
                result = self._run(user_message, history, actor, deadline, cancel, trace)
            except AgentRunFailed as exc:
                exc.result.run_id = run_id
                self._write_trace(trace, exc.result, started, error=str(exc))
                raise
            except Exception as exc:
                # Backstop for a bug in the loop itself: anything that escaped
                # the guards around each provider call and tool dispatch still
                # leaves the boundary as a typed failure, so the caller never
                # sees a raw exception (#103). Those guards carry the partial
                # trajectory; a loop bug can strike with the transcript half
                # built, so none is trusted here.
                logger.exception(
                    "agent_run_crashed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                failed = self._internal_failed(exc, [], 0, [])
                failed.result.run_id = run_id
                self._write_trace(
                    trace, failed.result, started, error=f"{type(exc).__name__}: {exc}"
                )
                raise failed from exc
            result.run_id = run_id
            self._write_trace(trace, result, started)
            return result

    def _write_trace(
        self,
        trace: run_trace.RunTrace,
        result: AgentRunResult,
        started: float,
        *,
        error: str = "",
    ) -> None:
        """Append the run's record, when tracing is on. Never raises.

        The trace is diagnostics: a full disk, a bad path or an unserialisable
        value is logged as a warning and the run's outcome stands.
        """
        if self._tracer is None:
            return
        try:
            record = trace.record(
                result,
                limit=_STOP_LIMITS.get(result.stop_reason),
                error=error,
                total_ms=_ms_since(started),
            )
            self._tracer.write(record)
        except Exception as exc:
            logger.warning(
                "agent_trace_write_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def _chat(
        self,
        trace: run_trace.RunTrace,
        phase: run_trace.CallPhase,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None,
        max_tokens: int,
        timeout: float | None,
    ) -> ChatResult:
        """One provider call, metered into the run's trace however it ends.

        Re-raises whatever the provider raised: the caller's handling is
        unchanged, the trace only watches.
        """
        call = run_trace.ModelCall(phase=phase)
        trace.calls.append(call)
        started = perf_counter()
        try:
            # The phase rides a ContextVar to the provider's context capture
            # (#401), which keys each captured call to this trace entry.
            with model_phase(phase):
                result = self._provider.chat(
                    messages, tools=tools, max_tokens=max_tokens, timeout=timeout
                )
        except ToolCallArgumentsError as exc:
            call.status = "bad_args"
            call.failure = f"{type(exc).__name__}: {exc}"
            call.meter(exc.usage, exc.server)
            raise
        except Exception as exc:
            call.status = "failed"
            call.failure = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            call.ms = _ms_since(started)
        call.status = "truncated" if result.finish_reason == _FINISH_LENGTH else "ok"
        call.finish_reason = result.finish_reason
        call.tool_calls = len(result.tool_calls)
        call.meter(result.usage, result.server)
        return result

    def _run(
        self,
        user_message: str,
        history: Sequence[dict[str, Any]] | None,
        actor: str,
        deadline: float | None,
        cancel: threading.Event | None,
        trace: run_trace.RunTrace,
    ) -> AgentRunResult:
        specs = registry.tool_specs()
        system_prompt = SYSTEM_PROMPT
        # Built once, as of the run's start (#412): the agent's own writes
        # after this are in its tool results, not here.
        block = state_block.build(date.today())
        trace.state_block = block
        user_turn = state_block.with_state(block, user_message)
        read_only = registry.read_only_tool_names()
        trace.system_prompt_sha256 = SYSTEM_PROMPT_SHA256
        trace.summary_prompt_sha256 = SUMMARY_PROMPT_SHA256
        trace.tool_schemas_sha256 = run_trace.tool_schemas_sha256(specs)
        trace.tools_offered = len(specs)
        # Last gate before any provider request (#244). The service layer
        # already applied this policy when it built the history, but the loop
        # is the only place that can measure the request's real overhead —
        # system prompt, tool schemas, and this turn — so it re-applies the
        # same function with those figures. History reaching here over budget
        # (a delegate caller, a grown tool registry) is trimmed deterministically
        # rather than turned into an impossible request.
        windowed = context_budget.fit_history(
            history or [],
            budget_tokens=context_budget.history_token_budget(
                prompt_overhead_tokens=(
                    context_budget.estimate_tokens(system_prompt)
                    + sum(
                        context_budget.estimate_tokens(spec.model_dump_json())
                        for spec in specs
                    )
                    + context_budget.estimate_tokens(user_turn)
                )
            ),
        )
        trace.history_given = len(history or [])
        trace.history_kept = list(windowed)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *windowed,
            {"role": "user", "content": user_turn},
        ]
        records: list[ToolCallRecord] = []
        ctx = _RunContext(
            user_message=user_message, deadline=deadline, cancel=cancel, trace=trace
        )
        corrections = 0
        iterations = 0
        # Calls that reached the registry this run (the cap's count), and every
        # exchange the run has seen (the stall rule's memory).
        dispatched = 0
        seen: set[tuple[str, str, str]] = set()
        logger.info(
            "agent_run_started",
            tools=len(specs),
            history_messages=len(windowed),
            history_dropped=len(history or []) - len(windowed),
        )
        for iteration in range(1, self._max_iterations + 1):
            iterations = iteration
            if cancel is not None and cancel.is_set():
                # Stopped before this turn's provider call. ``iteration - 1``
                # provider turns actually ran.
                return self._finish(
                    "cancelled", None, records, iteration - 1, messages
                )
            if deadline is not None and time.monotonic() >= deadline:
                # Out of time before this turn's provider call — stop with the
                # tool calls that already ran (they stay committed and undoable).
                raise self._out_of_time(records, iteration, messages)
            call_timeout = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            if iteration == 1 and call_timeout is not None:
                # The budget left as the first model call starts, for the trace.
                trace.limits["deadline_s"] = round(call_timeout, 1)
            try:
                result = self._chat(
                    trace,
                    "turn",
                    messages,
                    tools=specs,
                    max_tokens=self._max_tokens,
                    timeout=call_timeout,
                )
            except ToolCallArgumentsError as exc:
                # The turn is unusable — its tool calls can't round-trip into
                # history — so the correction goes back as a user-role message
                # instead of a tool result.
                corrections += 1
                trace.corrections = corrections
                logger.warning(
                    "agent_unparseable_tool_call",
                    tool=exc.tool_name,
                    error=str(exc),
                    corrections=corrections,
                )
                if corrections > self._max_corrections:
                    return self._limit_stop("correction_limit", ctx, records, iteration, messages)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your tool call failed before execution: {exc}. "
                            "Call the tool again with corrected JSON arguments."
                        ),
                    }
                )
                continue
            except ProviderError as exc:
                # The provider fault ends the run, but the tool calls that ran
                # before it are already committed — keep their record truthful.
                if deadline is not None and time.monotonic() >= deadline:
                    raise self._out_of_time(records, iteration, messages) from exc
                raise self._provider_failed(exc, records, iteration, messages) from exc
            except Exception as exc:
                # The provider raised something it does not promise (a bug in
                # it, not a fault it reports). Between turns the transcript is
                # whole, so end the run as an internal error with the calls
                # that already ran and committed (#456) — the same guard the
                # tool dispatch has, not the backstop in ``run``.
                logger.exception(
                    "agent_provider_crashed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise self._internal_failed(exc, records, iteration, messages) from exc
            truncated = result.finish_reason == _FINISH_LENGTH
            if truncated:
                logger.warning(
                    "agent_turn_truncated",
                    max_tokens=self._max_tokens,
                    tool_calls=len(result.tool_calls),
                )
            if not result.tool_calls:
                if truncated:
                    # Cut off by the cap: the fragment is not a reply. Drop it
                    # (never returned, never in the transcript) and end the
                    # run under its own stop reason.
                    return self._limit_stop("truncated", ctx, records, iteration, messages)
                # A text turn terminates the loop, even when content is empty.
                messages.append(result.to_message())
                return self._finish("completed", result.content, records, iteration, messages)
            if truncated:
                # The calls arrived whole and run; only the cut-off text beside
                # them is dropped from history.
                result = result.model_copy(update={"content": None})
            messages.append(result.to_message())
            if not trace.wrote:
                # Lookup round trips (#412): turns of only reads before the
                # first turn that asks for anything else.
                if all(call.name in read_only for call in result.tool_calls):
                    trace.lookup_turns += 1
                else:
                    trace.wrote = True
            schema_error_this_turn = False
            progressed = False
            over_cap = False
            for index, call in enumerate(result.tool_calls):
                if cancel is not None and cancel.is_set():
                    # The user stopped the run before this call started (most
                    # often during the provider call that proposed it). Record
                    # the refused calls and stop — never start a tool after
                    # Stop, even one the model already asked for.
                    self._skip_remaining(
                        result.tool_calls[index:],
                        records,
                        messages,
                        error=_CANCELLED_ERROR,
                        reason="cancelled",
                    )
                    return self._finish(
                        "cancelled", None, records, iteration, messages
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    # Out of time before *starting* this call. Record the calls
                    # we refused (truthful trajectory: they never touched the
                    # service layer) and stop — a mutating tool must not begin
                    # after the request budget is already spent.
                    self._skip_remaining(
                        result.tool_calls[index:],
                        records,
                        messages,
                        error=_SKIPPED_ERROR,
                        reason="deadline_exceeded",
                    )
                    raise self._out_of_time(records, iteration, messages)
                if dispatched >= self._max_tool_calls:
                    # Past the run's cap: this call and the rest of the turn are
                    # answered "not run" and never dispatched. They are neither
                    # progress nor a stall — they never ran.
                    self._skip_remaining(
                        result.tool_calls[index:],
                        records,
                        messages,
                        error=_TOOL_CALL_LIMIT_ERROR,
                        reason="tool_call_limit",
                    )
                    over_cap = True
                    break
                dispatched += 1
                try:
                    record, feedback, schema_error = self._dispatch(call, actor)
                except Exception as exc:
                    # A bug or an infrastructure failure (e.g. an SQLite
                    # OperationalError), not something the model can correct.
                    # End the run with a truthful partial trajectory — including
                    # this call — so the caller can persist it (#103).
                    self._record_internal_error(call, records, messages, exc)
                    # The rest of the batch never starts; answer it as not run
                    # so every call in the saved turn has exactly one answer.
                    self._skip_remaining(
                        result.tool_calls[index + 1 :],
                        records,
                        messages,
                        error=_CRASHED_BATCH_ERROR,
                        reason="internal_error",
                    )
                    raise self._internal_failed(
                        exc, records, iteration, messages
                    ) from exc
                records.append(record)
                messages.append(tool_result_message(call.id, feedback))
                schema_error_this_turn = schema_error_this_turn or schema_error
                # Judged on what came back, not the call alone: a repeat that
                # answers differently (a list after a write) is new work.
                exchange = _exchange_key(call.name, call.arguments, feedback)
                progressed = progressed or exchange not in seen
                seen.add(exchange)
            if over_cap:
                # The next turn could only be refused more of the same.
                return self._limit_stop(
                    "tool_call_limit", ctx, records, iteration, messages
                )
            if schema_error_this_turn:
                corrections += 1
                trace.corrections = corrections
                if corrections > self._max_corrections:
                    return self._limit_stop("correction_limit", ctx, records, iteration, messages)
                # A malformed call is what the correction budget is for, so a
                # turn with one is never judged a stall.
            elif not progressed:
                # Every call this turn repeated an exchange the run had already
                # seen: another turn can bring nothing new back (#398).
                logger.warning("agent_run_no_progress", tool_calls=len(result.tool_calls))
                return self._limit_stop("no_progress", ctx, records, iteration, messages)
        return self._limit_stop("max_iterations", ctx, records, iterations, messages)

    def _limit_stop(
        self,
        stop_reason: _StopReason,
        ctx: _RunContext,
        records: list[ToolCallRecord],
        iterations: int,
        messages: list[dict[str, Any]],
    ) -> AgentRunResult:
        """End the run at a limit — with a reply from what ran, when anything did (#399).

        The stop reason is kept as is (delegates and the UI read it); only the
        reply changes. When no call reached the service layer there is nothing
        verified to say, so the reply stays ``None`` and the client shows its
        stop line, exactly as before.
        """
        reply = self._summarise(stop_reason, ctx, records)
        if reply is not None:
            messages.append({"role": "assistant", "content": reply})
        return self._finish(stop_reason, reply, records, iterations, messages)

    def _summarise(
        self,
        stop_reason: _StopReason,
        ctx: _RunContext,
        records: list[ToolCallRecord],
    ) -> str | None:
        """One tool-free model call that says what a limit-stopped run did.

        It reads the code-built record (:mod:`app.ai.run_record`), never the
        run's transcript, under the note that whatever the record does not show
        as done was not done. Bounded like every call: ``max_tokens`` and the
        time left before the run's deadline. Anything short of a whole answer —
        no time left, a Stop, a provider failure, a reply the cap cut off, an
        empty one — returns ``None``: the run keeps today's stop line rather
        than a fragment or an error.
        """
        if not run_record.any_ran(records):
            return None
        if ctx.cancel is not None and ctx.cancel.is_set():
            return None
        timeout: float | None = None
        if ctx.deadline is not None:
            timeout = ctx.deadline - time.monotonic()
            if timeout <= 0:
                logger.warning("agent_summary_skipped", reason="deadline_exceeded")
                return None
        request = run_record.summary_messages(
            ctx.user_message,
            records,
            stop_reason,
            _GLOBAL_PERSONALITY,
            date.today(),
        )
        try:
            result = self._chat(
                ctx.trace,
                "summary",
                request,
                tools=None,
                max_tokens=self._summary_max_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            # A provider fault, a timeout or a bug: the run's work is already
            # committed and its record stands, so the summary is dropped, never
            # the run.
            logger.warning(
                "agent_summary_failed",
                stop_reason=stop_reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        if result.finish_reason == _FINISH_LENGTH:
            logger.warning("agent_summary_truncated", stop_reason=stop_reason)
            return None
        reply = (result.content or "").strip()
        if result.tool_calls or not reply:
            logger.warning(
                "agent_summary_unusable",
                stop_reason=stop_reason,
                tool_calls=len(result.tool_calls),
            )
            return None
        logger.info("agent_summary", stop_reason=stop_reason, chars=len(reply))
        ctx.trace.summarised = True
        return reply

    @staticmethod
    def _skip_remaining(
        calls: Sequence[ToolCall],
        records: list[ToolCallRecord],
        messages: list[dict[str, Any]],
        *,
        error: str,
        reason: str,
    ) -> None:
        """Record calls the deadline or a Stop kept us from starting, and answer them.

        Each undispatched call still gets a ``role: tool`` message so the
        transcript stays well-formed (every tool call has its result) for the
        persisted trajectory and any later turn built on this history.
        """
        for call in calls:
            record = ToolCallRecord(
                tool=call.name,
                arguments=call.arguments,
                error=error,
            )
            records.append(record)
            messages.append(tool_result_message(call.id, _refusal_text(error, RETRY_NEVER)))
            logger.warning("agent_tool_call_skipped", tool=call.name, reason=reason)

    @staticmethod
    def _record_internal_error(
        call: ToolCall,
        records: list[ToolCallRecord],
        messages: list[dict[str, Any]],
        exc: BaseException,
    ) -> None:
        """Record (and answer) the call whose dispatch raised unexpectedly.

        The transcript stays well-formed — every tool call keeps a paired
        ``role: tool`` message — and the exception is logged with the run's
        bound request id.
        """
        records.append(
            ToolCallRecord(
                tool=call.name, arguments=call.arguments, error=_INTERNAL_ERROR
            )
        )
        messages.append(
            tool_result_message(call.id, _refusal_text(_INTERNAL_ERROR, RETRY_NEVER))
        )
        logger.exception(
            "agent_tool_call_crashed",
            tool=call.name,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    def _dispatch(self, call: ToolCall, actor: str) -> tuple[ToolCallRecord, str, bool]:
        """Run one tool call as ``actor``.

        Returns the record, the feedback text for the model's ``role: tool``
        message, and whether the failure was schema-level (counts against the
        correction budget). Unexpected exceptions (bugs, DB failures) propagate
        to the caller — the loop only self-corrects what the model can fix —
        where they end the run as an ``internal_error`` failure (see ``_run``).
        """
        record = ToolCallRecord(tool=call.name, arguments=call.arguments)
        schema_error = False
        retry: Retry = RETRY_NEVER
        details: dict[str, Any] = {}
        try:
            outcome = registry.call_tool(call.name, call.arguments, actor=actor)
        except UnknownToolError as exc:
            # The offer is the offer: no arguments make an unknown name work.
            record.error = str(exc)
            schema_error = True
        except ValidationError as exc:
            record.error = f"{run_record.INVALID_ARGUMENTS_PREFIX}{_validation_summary(exc)}"
            retry = RETRY_DIFFERENT_ARGS
            schema_error = True
        except ToolRefusal as exc:
            # Domain rejection with a reason and the way forward (#413).
            record.error = exc.error
            retry = exc.retry
            details = exc.details
        except ToolError as exc:
            record.error = str(exc)
        else:
            record.result = _result_text(outcome)
            logger.info("agent_tool_call", tool=call.name, ok=True)
            return record, record.result, False
        logger.warning(
            "agent_tool_call",
            tool=call.name,
            ok=False,
            error=record.error,
            schema_error=schema_error,
        )
        return record, _refusal_text(record.error, retry, details), schema_error

    @staticmethod
    def _finish(
        stop_reason: _StopReason,
        reply: str | None,
        records: list[ToolCallRecord],
        iterations: int,
        messages: list[dict[str, Any]],
    ) -> AgentRunResult:
        logger.info(
            "agent_run_finished",
            stop_reason=stop_reason,
            iterations=iterations,
            tool_calls=len(records),
        )
        return AgentRunResult(
            reply=reply,
            stop_reason=stop_reason,
            tool_calls=records,
            iterations=iterations,
            messages=messages,
        )

    @classmethod
    def _out_of_time(
        cls,
        records: list[ToolCallRecord],
        iterations: int,
        messages: list[dict[str, Any]],
    ) -> AgentRunFailed:
        return cls._failed("timed_out", "agent run exceeded its time budget", records, iterations, messages)

    @classmethod
    def _provider_failed(
        cls,
        exc: ProviderError,
        records: list[ToolCallRecord],
        iterations: int,
        messages: list[dict[str, Any]],
    ) -> AgentRunFailed:
        return cls._failed("provider_error", f"agent run failed: {exc}", records, iterations, messages)

    @classmethod
    def _internal_failed(
        cls,
        exc: BaseException,
        records: list[ToolCallRecord],
        iterations: int,
        messages: list[dict[str, Any]],
    ) -> AgentRunFailed:
        return cls._failed(
            "internal_error",
            f"agent run failed unexpectedly: {type(exc).__name__}: {exc}",
            records,
            iterations,
            messages,
        )

    @staticmethod
    def _failed(
        stop_reason: _StopReason,
        message: str,
        records: list[ToolCallRecord],
        iterations: int,
        messages: list[dict[str, Any]],
    ) -> AgentRunFailed:
        """Package a failed run: the partial trajectory plus its HTTP status."""
        logger.warning(
            "agent_run_failed",
            stop_reason=stop_reason,
            iterations=iterations,
            tool_calls=len(records),
        )
        result = AgentRunResult(
            reply=None,
            stop_reason=stop_reason,
            tool_calls=records,
            iterations=iterations,
            messages=messages,
        )
        return AgentRunFailed(
            result, http_status=_FAILURE_STATUS[stop_reason], message=message
        )


def _exchange_key(name: str, arguments: dict[str, Any], answer: str) -> tuple[str, str, str]:
    """Identity of one exchange — the tool, its arguments and what it answered.

    The answer is part of the key because the call alone is not: the same
    lookup after a write comes back different, and that is progress. Sorted
    keys so the key is the exchange, not how the model happened to order the
    arguments; ``default=str`` so an odd value still yields a key rather than
    raising inside the loop.
    """
    return name, json.dumps(arguments, sort_keys=True, default=str), answer


def _refusal_text(
    error: str, retry: Retry, details: dict[str, Any] | None = None
) -> str:
    """A failed call's ``role: tool`` answer, the one shape every failure takes (#413)."""
    return json.dumps(
        {"ok": False, "error": error, "retry": retry, **(details or {})}, default=str
    )


def _ms_since(started: float) -> int:
    """Whole ms since a ``perf_counter`` reading. The trace's durations use
    their own clock, never ``time.monotonic``: that one is the deadline's, and
    the deadline tests script its readings one by one."""
    return round((perf_counter() - started) * 1000)


def _validation_summary(exc: ValidationError) -> str:
    """Pydantic's error list as one compact line the model can read."""
    return "; ".join(
        f"{'.'.join(str(loc) for loc in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )


def _result_text(value: Any) -> str:
    """A tool result as the text body of a ``role: tool`` message."""
    if isinstance(value, str):
        return value
    return json.dumps(_jsonable(value), default=str)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
