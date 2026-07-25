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
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel, ValidationError

from app.ai.providers.llamacpp import (
    ChatResult,
    ProviderError,
    ToolCall,
    ToolCallArgumentsError,
    ToolSpec,
    tool_result_message,
)
from app.tools import registry
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
_SKIPPED_ERROR = "not run: the agent run's time budget was exhausted before this call"


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
#   3. dynamic layers — today's date, injected per run.
# The vendored Glitch body is never edited here: fix drift by re-copying from
# agent-standard/ (../agent-standard/check-sync.sh).

# Layer 1 — app base prompt (app-owned behavioral contract). Upholds the
# standard's invariants: tools-only action, no invented results, deterministic
# truth, clarify-on-ambiguity.
_APP_BASE_PROMPT = """\
You are the assistant for Project Command Center (PCC), the user's project and \
task manager. You act only by calling the provided tools.

Rules:
- Look things up before writing: find ids with the list_*/get_*/search tools; never guess an id.
- Prefer the specific tool: complete_task to finish a task, update_task to edit its fields.
- Every delete is a soft delete into a restorable trash; there is no permanent delete.
- If a tool call is rejected, read the error and correct your next call.
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


def build_system_prompt(today: date) -> str:
    """Compose the layered system prompt: app base → global Glitch → date.

    PCC ships no app-flavor layer (nothing has earned it); the date is the only
    dynamic layer.
    """
    return (
        f"{_APP_BASE_PROMPT}\n\n"
        f"{_GLOBAL_PERSONALITY}\n\n"
        f"Today's date is {today.isoformat()}."
    )

_StopReason = Literal[
    "completed",
    "max_iterations",
    "correction_limit",
    "provider_error",
    "timed_out",
    "internal_error",
]

# HTTP status the route surfaces for each failure stop reason: a provider fault
# is an upstream 502; exhausting the time budget is a 504; an unexpected
# exception (a bug, a DB failure) is our own 500.
_FAILURE_STATUS: dict[_StopReason, int] = {
    "provider_error": 502,
    "timed_out": 504,
    "internal_error": 500,
}

# Recorded (and fed back) for a call whose dispatch blew up unexpectedly. The
# call may or may not have reached the service layer, so the record claims
# neither success nor a clean rejection (#103).
_INTERNAL_ERROR = "internal error: the tool call failed unexpectedly"


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
    """Outcome of one loop run. ``messages`` is the full transcript."""

    reply: str | None
    stop_reason: _StopReason
    tool_calls: list[ToolCallRecord]
    iterations: int
    messages: list[dict[str, Any]]


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
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self._provider = provider
        self._max_iterations = max_iterations
        self._max_corrections = max_corrections

    def run(
        self,
        user_message: str,
        *,
        history: Sequence[dict[str, Any]] | None = None,
        actor: str = LOOP_ACTOR,
        deadline: float | None = None,
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
        """
        bindings: dict[str, str] = {}
        if "request_id" not in structlog.contextvars.get_contextvars():
            bindings["request_id"] = uuid.uuid4().hex[:8]
        with structlog.contextvars.bound_contextvars(**bindings):
            try:
                return self._run(user_message, history, actor, deadline)
            except AgentRunFailed:
                raise
            except Exception as exc:
                # Backstop: anything unexpected that escaped the per-call guard
                # still leaves the boundary as a typed failure, so the caller
                # never sees a raw exception (#103). No trajectory survives this
                # path — the per-call guard is what carries the records.
                logger.exception(
                    "agent_run_crashed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise self._internal_failed(exc, [], 0, []) from exc

    def _run(
        self,
        user_message: str,
        history: Sequence[dict[str, Any]] | None,
        actor: str,
        deadline: float | None,
    ) -> AgentRunResult:
        specs = registry.tool_specs()
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": build_system_prompt(date.today()),
            },
            *(history or []),
            {"role": "user", "content": user_message},
        ]
        records: list[ToolCallRecord] = []
        corrections = 0
        iterations = 0
        logger.info(
            "agent_run_started", tools=len(specs), history_messages=len(history or [])
        )
        for iteration in range(1, self._max_iterations + 1):
            iterations = iteration
            if deadline is not None and time.monotonic() >= deadline:
                # Out of time before this turn's provider call — stop with the
                # tool calls that already ran (they stay committed and undoable).
                raise self._out_of_time(records, iteration, messages)
            call_timeout = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            try:
                result = self._provider.chat(messages, tools=specs, timeout=call_timeout)
            except ToolCallArgumentsError as exc:
                # The turn is unusable — its tool calls can't round-trip into
                # history — so the correction goes back as a user-role message
                # instead of a tool result.
                corrections += 1
                logger.warning(
                    "agent_unparseable_tool_call",
                    tool=exc.tool_name,
                    error=str(exc),
                    corrections=corrections,
                )
                if corrections > self._max_corrections:
                    return self._finish("correction_limit", None, records, iteration, messages)
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
            if not result.tool_calls:
                # A text turn terminates the loop, even when content is empty.
                messages.append(result.to_message())
                return self._finish("completed", result.content, records, iteration, messages)
            messages.append(result.to_message())
            schema_error_this_turn = False
            for index, call in enumerate(result.tool_calls):
                if deadline is not None and time.monotonic() >= deadline:
                    # Out of time before *starting* this call. Record the calls
                    # we refused (truthful trajectory: they never touched the
                    # service layer) and stop — a mutating tool must not begin
                    # after the request budget is already spent.
                    self._skip_remaining(
                        result.tool_calls[index:], records, messages
                    )
                    raise self._out_of_time(records, iteration, messages)
                try:
                    record, feedback, schema_error = self._dispatch(call, actor)
                except Exception as exc:
                    # A bug or an infrastructure failure (e.g. an SQLite
                    # OperationalError), not something the model can correct.
                    # End the run with a truthful partial trajectory — including
                    # this call — so the caller can persist it (#103).
                    self._record_internal_error(call, records, messages, exc)
                    raise self._internal_failed(
                        exc, records, iteration, messages
                    ) from exc
                records.append(record)
                messages.append(tool_result_message(call.id, feedback))
                schema_error_this_turn = schema_error_this_turn or schema_error
            if schema_error_this_turn:
                corrections += 1
                if corrections > self._max_corrections:
                    return self._finish("correction_limit", None, records, iteration, messages)
        return self._finish("max_iterations", None, records, iterations, messages)

    @staticmethod
    def _skip_remaining(
        calls: Sequence[ToolCall],
        records: list[ToolCallRecord],
        messages: list[dict[str, Any]],
    ) -> None:
        """Record calls the deadline stopped us from starting, and answer them.

        Each undispatched call still gets a ``role: tool`` message so the
        transcript stays well-formed (every tool call has its result) for the
        persisted trajectory and any later turn built on this history.
        """
        for call in calls:
            record = ToolCallRecord(
                tool=call.name,
                arguments=call.arguments,
                error=_SKIPPED_ERROR,
            )
            records.append(record)
            messages.append(tool_result_message(call.id, f"Error: {_SKIPPED_ERROR}"))
            logger.warning(
                "agent_tool_call_skipped", tool=call.name, reason="deadline_exceeded"
            )

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
        messages.append(tool_result_message(call.id, f"Error: {_INTERNAL_ERROR}"))
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
        try:
            outcome = registry.call_tool(call.name, call.arguments, actor=actor)
        except UnknownToolError as exc:
            record.error = str(exc)
            schema_error = True
        except ValidationError as exc:
            record.error = f"Invalid arguments: {_validation_summary(exc)}"
            schema_error = True
        except ToolError as exc:
            # Domain rejection with a reason the model can act on.
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
        return record, f"Error: {record.error}", schema_error

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
