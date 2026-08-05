"""Agent chat API: conversations, history, and the message → loop round trip.

``POST /agent/conversations/{id}/messages`` is the one model-calling endpoint:
it stores the user turn, runs the agent loop (synchronously — v1 is
non-streaming, see CURRENT.md), stores the assistant turn, and returns both.
It is rate-limited per client IP; every log line of a run carries the
request ID the middleware bound.
"""

from __future__ import annotations

import time
from collections.abc import Generator, Sequence

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.ai.loop import AgentLoop, AgentRunFailed, resolve_actor
from app.ai.providers.llamacpp import provider_from_settings
from app.api.conversation_locks import conversation_idle_lock, conversation_run_lock
from app.api.rate_limit import rate_limit
from app.config import get_settings
from app.db.models import Conversation
from app.db.session import get_db, get_db_write
from app.schemas.common import EntityId, PaginationOffset
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


# The sidebar's page size, and the ceiling on one request. The sidebar grows
# its window by DEFAULT_CONVERSATION_LIMIT per "Load more", re-reading from
# offset 0 so a reordered list can't produce duplicates or gaps; the cap keeps
# the read bounded no matter what a client asks for.
DEFAULT_CONVERSATION_LIMIT = 50
MAX_CONVERSATION_LIMIT = 500


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations(
    limit: int = Query(default=DEFAULT_CONVERSATION_LIMIT, ge=1, le=MAX_CONVERSATION_LIMIT),
    offset: PaginationOffset = 0,
    db: Session = Depends(get_db),
) -> Sequence[Conversation]:
    return conversations_service.list_conversations(db, limit=limit, offset=offset)


@router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    data: ConversationCreate, db: Session = Depends(get_db_write)
) -> Conversation:
    conversation = conversations_service.create_conversation(db, title=data.title)
    db.commit()
    db.refresh(conversation)
    logger.info("conversation_created", conversation_id=conversation.id)
    return conversation


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: EntityId, db: Session = Depends(get_db)
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
def delete_conversation(conversation_id: EntityId, db: Session = Depends(get_db_write)) -> None:
    """Soft-delete an idle conversation; **409** while a run is in flight (#149).

    Deletion takes the conversation's run lock, so it is serialized against
    ``post_message`` instead of racing it. Without that, a DELETE landing after
    the user turn committed would soft-delete the thread while the model was
    still generating; the run would then commit its assistant turn — and its
    tool-call trajectory or failure record — into a thread the caller can no
    longer read (GET → 404). The lock is taken with a zero wait: a run can take
    minutes, and blocking the DELETE that long is worse than telling the caller
    to retry. The write session is touched only inside the lock, keeping the
    conversation-lock-then-SQLite-write-lock order of #91.
    """
    with conversation_idle_lock(conversation_id):
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
    conversation_id: EntityId,
    data: MessageCreate,
    db: Session = Depends(get_db_write),
    reader: Session = Depends(get_db),
    loop: AgentLoop = Depends(get_agent_loop),
    x_agent_actor: str | None = Header(default=None),
) -> MessageExchange:
    """Store the user turn, run the loop, store and return the assistant turn.

    Runs on this conversation are serialized (``conversation_run_lock``): a
    second concurrent message waits for the first to finish — so it reads
    history that includes the first reply — or gets 409 if the wait exceeds the
    run budget. The single deadline covers both the wait and the run, keeping
    the whole request under the proxy timeout — with one caveat: the loop never
    *starts* a provider or tool call past the deadline, but a synchronous tool
    already in flight runs to completion, so the request can overshoot the
    budget by at most one tool call's duration (see ``AgentLoop``). Size
    ``agent_run_budget_seconds`` with headroom for that. The 404 check happens on the
    read session (``reader``) before the wait; no write transaction may be open
    while waiting, or the two locks invert (see below).

    The user message is committed *before* the loop runs: the loop's tool calls
    open their own sessions (write lock contention otherwise), and a failure
    must not swallow what the user said. On failure the loop raises
    ``AgentRunFailed`` carrying the tool calls that *did* run; we persist that
    partial trajectory as a truthful assistant turn (``content`` null, a
    ``provider_error``/``timed_out``/``internal_error`` stop reason) before
    surfacing the error, so the conversation always reflects what actually
    happened — including when a tool dispatch fails unexpectedly (#103), which
    the loop converts into an ``internal_error`` 500 rather than letting the
    raw exception leave the user turn unpaired.

    ``X-Agent-Actor`` lets a trusted delegate caller (conductor) attribute the
    run's mutations to itself in the audit trail; an absent or unrecognized
    value falls back to the loop's default identity (see ``resolve_actor``).
    """
    _get_or_404(reader, conversation_id)
    actor = resolve_actor(x_agent_actor)
    budget = get_settings().agent_run_budget_seconds
    deadline = time.monotonic() + budget

    with conversation_run_lock(conversation_id, wait_seconds=budget):
        # First statement on the write session, so BEGIN IMMEDIATE — and the
        # SQLite write lock — is taken *inside* the conversation lock. Doing the
        # existence check on ``db`` instead would hold the write lock across the
        # wait above, and the holder's tool writes would then deadlock on us
        # until busy_timeout (#91).
        conversation = _get_or_404(db, conversation_id)
        history = conversations_service.history_for_loop(db, conversation.id)
        user_message = conversations_service.append_user_message(
            db, conversation, data.content
        )
        db.commit()

        try:
            run = loop.run(
                data.content, history=history, actor=actor, deadline=deadline
            )
        except AgentRunFailed as exc:
            logger.error(
                "agent_run_failed",
                conversation_id=conversation_id,
                stop_reason=exc.result.stop_reason,
                error=str(exc),
            )
            conversations_service.append_assistant_message(db, conversation, exc.result)
            db.commit()
            raise HTTPException(
                status_code=exc.http_status, detail=str(exc)
            ) from exc

        assistant_message = conversations_service.append_assistant_message(
            db, conversation, run
        )
        db.commit()
        return MessageExchange(
            user_message=MessageRead.model_validate(user_message),
            assistant_message=MessageRead.model_validate(assistant_message),
        )
