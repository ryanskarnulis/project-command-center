"""Agent conversation persistence: threads and their user/assistant turns.

The only write path for ``conversations`` / ``conversation_messages`` (routes
and any future tool are peers calling in here). Messages are immutable once
appended; soft delete is conversation-level only. The loop itself lives in
``app/ai/loop.py`` — this module persists what it consumed and produced.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.loop import AgentRunResult
from app.db.models import Conversation, ConversationMessage, ConversationRole, utcnow
from app.services import activity
from app.services.common import active, soft_delete

logger = structlog.get_logger(__name__)

# Auto-titles derived from the first user message are cut here, on a word
# boundary where one exists.
MAX_DERIVED_TITLE_LENGTH = 60


def list_conversations(
    db: Session, *, limit: int = 50, offset: int = 0
) -> Sequence[Conversation]:
    """A window of active conversations, most recently touched first.

    The ordering is total (``updated_at`` desc, then ``id`` desc), so
    successive ``offset`` windows over an unchanged list have no duplicates
    and no gaps. A new turn bumps a conversation's ``updated_at`` and reorders
    the list, so a paging client should re-read from ``offset=0`` after a
    write rather than stitching a stale window onto a fresh page.
    """
    return (
        db.execute(
            active(Conversation)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )


def get_conversation(db: Session, conversation_id: int) -> Conversation | None:
    return db.execute(
        active(Conversation).where(Conversation.id == conversation_id)
    ).scalar_one_or_none()


def create_conversation(db: Session, *, title: str | None = None) -> Conversation:
    conversation = Conversation(title=title)
    db.add(conversation)
    db.flush()
    db.refresh(conversation)
    activity.record_event(
        db,
        project_id=None,
        entity_type="conversation",
        entity_id=conversation.id,
        action="created",
        summary=(
            f'Conversation "{title}" started' if title else "Conversation started"
        ),
    )
    return conversation


def soft_delete_conversation(db: Session, conversation: Conversation) -> None:
    """Trash a conversation (messages ride along; nothing is hard-deleted)."""
    soft_delete(conversation)
    db.flush()
    activity.record_event(
        db,
        project_id=None,
        entity_type="conversation",
        entity_id=conversation.id,
        action="deleted",
        summary=f'Conversation "{conversation.title or conversation.id}" deleted',
    )


def list_messages(
    db: Session, conversation_id: int
) -> Sequence[ConversationMessage]:
    """A conversation's messages, oldest first."""
    return (
        db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.id)
        )
        .scalars()
        .all()
    )


def history_for_loop(db: Session, conversation_id: int) -> list[dict[str, str]]:
    """Prior turns as provider messages for ``AgentLoop.run(history=…)``.

    Text turns only: tool calls/results are persisted for display and audit
    but deliberately not round-tripped into future model context — replaying
    stale tool transcripts bloats the local model's window, and the loop
    re-reads live state through its tools instead. Assistant turns that ended
    without a reply (iteration/correction limit) are skipped.
    """
    return [
        {"role": message.role.value, "content": message.content}
        for message in list_messages(db, conversation_id)
        if message.content is not None
    ]


def append_user_message(
    db: Session, conversation: Conversation, content: str
) -> ConversationMessage:
    """Store one user turn; an untitled conversation is titled from it."""
    if conversation.title is None:
        conversation.title = _derive_title(content)
    message = ConversationMessage(
        conversation_id=conversation.id,
        role=ConversationRole.user,
        content=content,
    )
    conversation.updated_at = utcnow()
    db.add(message)
    db.flush()
    db.refresh(message)
    logger.info(
        "conversation_user_message",
        conversation_id=conversation.id,
        message_id=message.id,
    )
    return message


def append_assistant_message(
    db: Session, conversation: Conversation, run: AgentRunResult
) -> ConversationMessage:
    """Store the loop's outcome as one assistant turn."""
    message = ConversationMessage(
        conversation_id=conversation.id,
        role=ConversationRole.assistant,
        content=run.reply,
        tool_calls=(
            [record.model_dump() for record in run.tool_calls]
            if run.tool_calls
            else None
        ),
        stop_reason=run.stop_reason,
    )
    conversation.updated_at = utcnow()
    db.add(message)
    db.flush()
    db.refresh(message)
    logger.info(
        "conversation_assistant_message",
        conversation_id=conversation.id,
        message_id=message.id,
        stop_reason=run.stop_reason,
        tool_calls=len(run.tool_calls),
    )
    return message


def _derive_title(content: str) -> str:
    """First user message, cut to a title on a word boundary."""
    first_line = content.strip().splitlines()[0]
    if len(first_line) <= MAX_DERIVED_TITLE_LENGTH:
        return first_line
    cut = first_line[: MAX_DERIVED_TITLE_LENGTH + 1]
    head, _, _ = cut.rpartition(" ")
    return (head or cut[:MAX_DERIVED_TITLE_LENGTH]).rstrip() + "…"
