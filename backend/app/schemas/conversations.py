from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

from app.db.models import ConversationRole
from app.schemas.common import MutationModel, NonBlankStr, UTCDateTime

# Cap on a user-chosen conversation title. Derived titles stop at 60; a typed
# one gets room for a sentence and no more — it has to fit a 44px title row.
MAX_CONVERSATION_TITLE_LENGTH = 120

# Cap on one chat turn. Generous for typed input while bounding what a run
# feeds the local model's context window.
MAX_AGENT_MESSAGE_LENGTH = 8_000

AgentMessageText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_AGENT_MESSAGE_LENGTH
    ),
]


class ConversationCreate(MutationModel):
    # Optional: an untitled conversation is titled from its first user message.
    title: NonBlankStr | None = None


class ConversationUpdate(MutationModel):
    """A rename — the only user-editable field on a conversation."""

    title: Annotated[NonBlankStr, StringConstraints(max_length=MAX_CONVERSATION_TITLE_LENGTH)]


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    created_at: UTCDateTime
    # Touched on every appended message — the conversation list's recency order.
    updated_at: UTCDateTime


# Delegate-api v1.2 (#527): at most this many refs and facts ride a message.
MAX_CONTEXT_REFS = 20
MAX_CONTEXT_FACTS = 5
MAX_FACT_CHARS = 200

_ContextText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_FACT_CHARS)
]


class ContextRef(MutationModel):
    """An entity ref in the record's shape: what the person's words point at."""

    app: _ContextText
    kind: _ContextText
    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    title: _ContextText | None = None
    untrusted: bool = False
    # The person's pick from a list the app gave (delegate v1.2, conductor#196).
    picked: bool = False


class MessageContext(MutationModel):
    """Data beside the person's words, never instructions (delegate-api v1.2)."""

    refs: Annotated[list[ContextRef], Field(max_length=MAX_CONTEXT_REFS)] = []
    facts: Annotated[list[_ContextText], Field(max_length=MAX_CONTEXT_FACTS)] = []


class MessageCreate(MutationModel):
    content: AgentMessageText
    # Delegate-api v1.2 (#527); absent on a v1.1 caller.
    context: MessageContext | None = None
    mode: Literal["read"] | None = None


class ToolCallRead(BaseModel):
    """One dispatched tool call as persisted on an assistant message.

    Mirrors ``app/ai/loop.py::ToolCallRecord``; exactly one of ``result`` /
    ``error`` is set.
    """

    tool: str
    arguments: dict[str, Any]
    result: str | None = None
    error: str | None = None
    refusal: dict[str, Any] | None = None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: ConversationRole
    content: str | None
    tool_calls: list[ToolCallRead] | None = None
    stop_reason: str | None = None
    created_at: UTCDateTime
    # The question a gated call armed on a delegate run (#503): ``{token,
    # tool, question, preview, expires_at}``. Only on the reply that asked
    # (and its replay); history reads carry ``None``.
    confirm_required: dict[str, Any] | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def record(self) -> list[dict[str, Any]] | None:
        """The turn's typed record (delegate-api v1.1, #502): one receipt per
        tool call, ``[]`` when none ran; ``None`` on a user turn. Built by code
        from ``tool_calls`` on every read, never stored."""
        if self.role != ConversationRole.assistant:
            return None
        from app.ai import receipts  # app.ai imports the tool registry

        calls = [call.model_dump() for call in self.tool_calls or []]
        return receipts.dump(receipts.build(calls))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_input(self) -> dict[str, Any] | None:
        """What the turn asked the person (delegate-api v1.2, #527): a landed
        ``ask_user`` in a run that did nothing, as ``{question, candidates}``.
        Built by code from ``tool_calls`` on every read; ``None`` otherwise."""
        if self.role != ConversationRole.assistant:
            return None
        from app.ai import delegate_context

        calls = [call.model_dump() for call in self.tool_calls or []]
        return delegate_context.needs_input(calls)


class ConversationDetail(ConversationRead):
    """A conversation with one bounded page of its history, oldest first.

    The page is the newest ``limit`` messages by default (chat reads the tail
    first); ``before_id`` walks backwards from there. A thread's full
    transcript stays reachable — ``has_more`` says whether an older page
    exists, ``message_count`` is the total — but no single response is
    unbounded (#244).
    """

    messages: list[MessageRead]
    # Total persisted messages in the thread, across every page.
    message_count: int
    # True when messages older than ``messages[0]`` exist.
    has_more: bool
    # A run is in flight on this conversation right now (#358) — the user turn
    # is already in ``messages``, its reply is not yet. ``run_started_at`` is
    # its start (UTC), None when idle.
    run_in_flight: bool
    run_started_at: UTCDateTime | None


class MessageExchange(BaseModel):
    """What one ``POST …/messages`` produced: the stored user turn and the
    assistant turn the loop answered it with."""

    user_message: MessageRead
    assistant_message: MessageRead


class RunCancelRead(BaseModel):
    """Outcome of a Stop request (#324).

    ``cancel_requested`` is True when a run was in flight and has been asked to
    stop; it stops at its next provider/tool boundary, and the pending message
    POST then returns with stop reason ``cancelled``. False means nothing was
    running — nothing is recorded, so a late Stop can never cancel a later run.
    """

    cancel_requested: bool
