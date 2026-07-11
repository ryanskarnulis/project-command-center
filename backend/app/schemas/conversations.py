from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.models import ConversationRole
from app.schemas.common import NonBlankStr, UTCDateTime

# Cap on one chat turn. Generous for typed input while bounding what a run
# feeds the local model's context window.
MAX_AGENT_MESSAGE_LENGTH = 8_000

AgentMessageText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_AGENT_MESSAGE_LENGTH
    ),
]


class ConversationCreate(BaseModel):
    # Optional: an untitled conversation is titled from its first user message.
    title: NonBlankStr | None = None


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    created_at: UTCDateTime
    # Touched on every appended message — the conversation list's recency order.
    updated_at: UTCDateTime


class MessageCreate(BaseModel):
    content: AgentMessageText


class ToolCallRead(BaseModel):
    """One dispatched tool call as persisted on an assistant message.

    Mirrors ``app/ai/loop.py::ToolCallRecord``; exactly one of ``result`` /
    ``error`` is set.
    """

    tool: str
    arguments: dict[str, Any]
    result: str | None = None
    error: str | None = None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: ConversationRole
    content: str | None
    tool_calls: list[ToolCallRead] | None = None
    stop_reason: str | None = None
    created_at: UTCDateTime


class ConversationDetail(ConversationRead):
    """A conversation with its full message history, oldest first."""

    messages: list[MessageRead]


class MessageExchange(BaseModel):
    """What one ``POST …/messages`` produced: the stored user turn and the
    assistant turn the loop answered it with."""

    user_message: MessageRead
    assistant_message: MessageRead
