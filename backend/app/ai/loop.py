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
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel, ValidationError

from app.ai.providers.llamacpp import (
    ChatResult,
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

_SYSTEM_PROMPT = """\
You are the Project Command Center (PCC) assistant. You manage the user's \
projects and tasks by calling the provided tools.

Rules:
- Look things up before writing: find ids with the list_*/get_*/search tools; never guess an id.
- Prefer the specific tool: complete_task to finish a task, update_task to edit its fields.
- Every delete is a soft delete into a restorable trash; there is no permanent delete.
- If a tool call is rejected, read the error and correct your next call.
- When the work is done (or turns out to be impossible), reply with plain text: a short \
summary of what you did or found. State only what the tool results confirm.

Today's date is {today}."""

_StopReason = Literal["completed", "max_iterations", "correction_limit"]


class ChatProvider(Protocol):
    """What the loop needs from a provider — matched by ``LlamaCppProvider``."""

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
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
    ) -> AgentRunResult:
        """Run the loop for one user message. Always returns; never spins.

        Binds a request ID for the whole run unless the caller (e.g. the HTTP
        middleware, come slice 2) already bound one — every tool call and
        provider log line of the run then carries the same ID.
        """
        bindings: dict[str, str] = {}
        if "request_id" not in structlog.contextvars.get_contextvars():
            bindings["request_id"] = uuid.uuid4().hex[:8]
        with structlog.contextvars.bound_contextvars(**bindings):
            return self._run(user_message, history)

    def _run(
        self, user_message: str, history: Sequence[dict[str, Any]] | None
    ) -> AgentRunResult:
        specs = registry.tool_specs()
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT.format(today=date.today().isoformat()),
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
            try:
                result = self._provider.chat(messages, tools=specs)
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
            if not result.tool_calls:
                # A text turn terminates the loop, even when content is empty.
                messages.append(result.to_message())
                return self._finish("completed", result.content, records, iteration, messages)
            messages.append(result.to_message())
            schema_error_this_turn = False
            for call in result.tool_calls:
                record, feedback, schema_error = self._dispatch(call)
                records.append(record)
                messages.append(tool_result_message(call.id, feedback))
                schema_error_this_turn = schema_error_this_turn or schema_error
            if schema_error_this_turn:
                corrections += 1
                if corrections > self._max_corrections:
                    return self._finish("correction_limit", None, records, iteration, messages)
        return self._finish("max_iterations", None, records, iterations, messages)

    def _dispatch(self, call: ToolCall) -> tuple[ToolCallRecord, str, bool]:
        """Run one tool call.

        Returns the record, the feedback text for the model's ``role: tool``
        message, and whether the failure was schema-level (counts against the
        correction budget). Unexpected exceptions (bugs, DB failures)
        propagate — the loop only self-corrects what the model can fix.
        """
        record = ToolCallRecord(tool=call.name, arguments=call.arguments)
        schema_error = False
        try:
            outcome = registry.call_tool(call.name, call.arguments, actor=LOOP_ACTOR)
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
