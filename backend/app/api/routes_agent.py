"""Agent chat API: conversations, history, and the message → loop round trip.

``POST /agent/conversations/{id}/messages`` is the one model-calling endpoint:
it stores the user turn, runs the agent loop (synchronously — v1 is
non-streaming, see CURRENT.md), stores the assistant turn, and returns both.
It is rate-limited per client IP; every log line of a run carries the
request ID the middleware bound.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.ai.loop import AgentLoop, resolve_actor
from app.ai.providers.llamacpp import ProviderError, provider_from_settings
from app.api.rate_limit import rate_limit
from app.db.models import Conversation
from app.db.session import get_db
from app.schemas.conversations import (
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageCreate,
    MessageExchange,
    MessageRead,
)
from app.services import conversations as conversations_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def get_agent_loop() -> Generator[AgentLoop, None, None]:
    """The loop over the configured provider; tests override this dependency."""
    provider = provider_from_settings()
    try:
        yield AgentLoop(provider)
    finally:
        provider.close()


def _get_or_404(db: Session, conversation_id: int) -> Conversation:
    conversation = conversations_service.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return conversation


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations(db: Session = Depends(get_db)) -> Sequence[Conversation]:
    return conversations_service.list_conversations(db)


@router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    data: ConversationCreate, db: Session = Depends(get_db)
) -> Conversation:
    conversation = conversations_service.create_conversation(db, title=data.title)
    db.commit()
    db.refresh(conversation)
    logger.info("conversation_created", conversation_id=conversation.id)
    return conversation


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int, db: Session = Depends(get_db)
) -> ConversationDetail:
    conversation = _get_or_404(db, conversation_id)
    return ConversationDetail(
        **ConversationRead.model_validate(conversation).model_dump(),
        messages=[
            MessageRead.model_validate(message)
            for message in conversations_service.list_messages(db, conversation.id)
        ],
    )


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)) -> None:
    conversation = _get_or_404(db, conversation_id)
    conversations_service.soft_delete_conversation(db, conversation)
    db.commit()
    logger.info("conversation_deleted", conversation_id=conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageExchange,
    dependencies=[
        Depends(rate_limit("agent_messages", per_min_attr="agent_messages_per_min"))
    ],
)
def post_message(
    conversation_id: int,
    data: MessageCreate,
    db: Session = Depends(get_db),
    loop: AgentLoop = Depends(get_agent_loop),
    x_agent_actor: str | None = Header(default=None),
) -> MessageExchange:
    """Store the user turn, run the loop, store and return the assistant turn.

    The user message is committed *before* the loop runs: the loop's tool
    calls open their own sessions (write lock contention otherwise), and a
    provider failure — surfaced as 502 — must not swallow what the user said.

    ``X-Agent-Actor`` lets a trusted delegate caller (conductor) attribute the
    run's mutations to itself in the audit trail; an absent or unrecognized
    value falls back to the loop's default identity (see ``resolve_actor``).
    """
    conversation = _get_or_404(db, conversation_id)
    actor = resolve_actor(x_agent_actor)
    history = conversations_service.history_for_loop(db, conversation.id)
    user_message = conversations_service.append_user_message(
        db, conversation, data.content
    )
    db.commit()

    try:
        run = loop.run(data.content, history=history, actor=actor)
    except ProviderError as exc:
        logger.error(
            "agent_run_failed", conversation_id=conversation_id, error=str(exc)
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"agent run failed: {exc}",
        ) from exc

    assistant_message = conversations_service.append_assistant_message(
        db, conversation, run
    )
    db.commit()
    return MessageExchange(
        user_message=MessageRead.model_validate(user_message),
        assistant_message=MessageRead.model_validate(assistant_message),
    )
