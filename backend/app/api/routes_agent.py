"""Agent chat API: conversations, history, and the message → loop round trip.

``POST /agent/conversations/{id}/messages`` is the one model-calling endpoint:
it stores the user turn, runs the agent loop (synchronously — v1 is
non-streaming by decision), stores the assistant turn, and returns both.
It is rate-limited per client IP; every log line of a run carries the
request ID the middleware bound. ``POST /agent/conversations/{id}/cancel``
asks that conversation's in-flight run to stop (#324).
"""

from __future__ import annotations

import time
from datetime import date
from collections.abc import Generator, Sequence
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.ai import confirmation, run_record
from app.ai.loop import (
    DELEGATE_ACTORS,
    AgentLoop,
    AgentRunFailed,
    AgentRunResult,
    resolve_actor,
)
from app.ai.providers.llamacpp import provider_from_settings
from app.api.conversation_locks import conversation_idle_lock, conversation_run_lock
from app.api.rate_limit import rate_limit
from app.api.run_cancel import active_run, request_cancel, run_started_at
from app.config import get_settings
from app.db.models import Conversation, ConversationMessage
from app.db.session import get_db, get_db_write
from app.schemas.common import MAX_SQLITE_INT, EntityId, PaginationOffset
from app.schemas.conversations import (
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessageExchange,
    MessageRead,
    RunCancelRead,
)
from app.schemas.glance import Glance
from app.services import conversations as conversations_service
from app.services import glance as glance_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/glance", response_model=Glance)
def glance(db: Session = Depends(get_db)) -> Glance:
    """Today's and overdue tasks and today's Focus, for conductor's ``look`` (#504).

    No arguments, no model call, read-only: it never takes the rate limit or a
    conversation lock, and writes nothing (no audit row).
    """
    return glance_service.get_glance(db, today=date.today())


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

# The thread view's page size, and the ceiling on one detail request (#244).
# A conversation's transcript grows without limit — messages are immutable and
# never pruned — so the detail response returns the newest page and lets the
# panel walk backwards with `before_id` rather than serializing and rendering
# every turn ever sent.
DEFAULT_MESSAGE_LIMIT = 100
MAX_MESSAGE_LIMIT = 500


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
    conversation_id: EntityId,
    limit: int = Query(default=DEFAULT_MESSAGE_LIMIT, ge=1, le=MAX_MESSAGE_LIMIT),
    before_id: int | None = Query(default=None, ge=1, le=MAX_SQLITE_INT),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    """One bounded page of a conversation, newest ``limit`` messages by default.

    ``before_id`` pages backwards (older than that message id). Message ids are
    monotonic and messages immutable, so pages never duplicate or skip a turn
    even while the thread grows underneath a reader.

    ``run_in_flight``/``run_started_at`` report a run currently holding this
    conversation (#358), so a page that did not start it — after a reload, or
    in another tab — can show it, offer Stop, and poll until it lands.
    """
    conversation = _get_or_404(db, conversation_id)
    started_at = run_started_at(conversation.id)
    messages = conversations_service.list_messages(
        db, conversation.id, limit=limit, before_id=before_id
    )
    return ConversationDetail(
        **ConversationRead.model_validate(conversation).model_dump(),
        messages=[MessageRead.model_validate(message) for message in messages],
        message_count=conversations_service.count_messages(db, conversation.id),
        has_more=bool(messages)
        and conversations_service.has_messages_before(
            db, conversation.id, messages[0].id
        ),
        run_in_flight=started_at is not None,
        run_started_at=started_at,
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


@router.post("/conversations/{conversation_id}/restore", response_model=ConversationRead)
def restore_conversation(
    conversation_id: EntityId, db: Session = Depends(get_db_write)
) -> Conversation:
    """Bring a soft-deleted conversation back; **404** unless it is in the trash.

    The undo behind the phone's swipe-to-delete (M07f): the delete lands
    immediately and this reverses it within the undo bar's window. A live
    conversation 404s here too — there is nothing to restore, and answering 200
    would let a stale undo claim it did something.
    """
    conversation = conversations_service.get_deleted_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not in trash"
        )
    conversations_service.restore_conversation(db, conversation)
    db.commit()
    db.refresh(conversation)
    logger.info("conversation_restored", conversation_id=conversation_id)
    return conversation


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
def update_conversation(
    conversation_id: EntityId,
    data: ConversationUpdate,
    db: Session = Depends(get_db_write),
) -> Conversation:
    """Rename a live conversation. Does not touch its recency."""
    conversation = _get_or_404(db, conversation_id)
    conversations_service.rename_conversation(db, conversation, data.title)
    db.commit()
    db.refresh(conversation)
    logger.info("conversation_renamed", conversation_id=conversation_id)
    return conversation


# An ``Idempotency-Key`` is the caller's name for one exchange (delegate-api
# v1.1); longer than this is a caller bug, not a key (chess uses the same cap).
MAX_IDEMPOTENCY_KEY_LENGTH = 128

# The failure stops and the status each answers with, so a replayed failure
# answers exactly as the first attempt did (``loop._FAILURE_STATUS``).
FAILURE_STATUS = {"provider_error": 502, "timed_out": 504, "internal_error": 500}


def _failure_response(
    status_code: int, detail: str, message: ConversationMessage | None
) -> JSONResponse:
    """A 5xx with the failed run's partial record (delegate-api v1.1, #502).

    Keeps FastAPI's ``detail`` for v1 clients and adds what the run did, so a
    caller never reads a write that landed as "unavailable, try again"
    (ryanskarnulis/conductor#60).
    """
    read = MessageRead.model_validate(message) if message is not None else None
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "stop_reason": read.stop_reason if read is not None else None,
            "assistant_message_id": read.id if read is not None else None,
            "record": read.record if read is not None else None,
        },
    )


def _replayed(
    content: str, asked: ConversationMessage, answered: ConversationMessage | None
) -> MessageExchange | JSONResponse:
    """The answer to a retried ``Idempotency-Key``: never a second run."""
    if asked.content != content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="that Idempotency-Key was already used for a different message",
        )
    if answered is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "the exchange under that Idempotency-Key did not complete; whatever "
                "it did has happened. Read the thread and send a new key"
            ),
        )
    failed = FAILURE_STATUS.get(answered.stop_reason or "")
    if failed is not None:
        return _failure_response(failed, f"agent run failed: {answered.stop_reason}", answered)
    return MessageExchange(
        user_message=MessageRead.model_validate(asked),
        assistant_message=_assistant_read(answered, confirmation.for_message(answered.id)),
    )


def _assistant_read(
    message: ConversationMessage, pending: confirmation.Pending | None
) -> MessageRead:
    """The assistant turn as read, with the confirmation it asked (#503)."""
    read = MessageRead.model_validate(message)
    if pending is None:
        return read
    return read.model_copy(update={"confirm_required": pending.wire()})


def _confirm_refused(kind: str) -> JSONResponse:
    """A ``Confirm-Token`` that can't be honoured: 409, nothing ran (#503)."""
    words = {
        "unknown": "that Confirm-Token isn't one this thread is waiting on",
        "expired": "that Confirm-Token expired; ask again",
        "used": "that Confirm-Token was already used",
        "stale": "what that Confirm-Token previewed has changed since; ask again",
    }
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": words.get(kind, kind), "confirm": kind},
    )


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
    idempotency_key: Annotated[
        str | None, Header(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)
    ] = None,
    confirm_token: Annotated[str | None, Header(min_length=1, max_length=128)] = None,
) -> MessageExchange | JSONResponse:
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

    A Stop (``POST …/cancel``, #324) ends the run at its next provider/tool
    boundary with stop reason ``cancelled``. That is a **200**, not an error:
    the user asked for it and got it, and the response carries the truthful
    partial turn — the tool calls that ran keep their undo — exactly like a
    ``max_iterations`` stop.

    **Delegate v1.1** (#502). Every assistant turn carries its typed
    ``record``; a 5xx body carries the failed run's partial record beside
    ``detail``. An ``Idempotency-Key`` is stored on the user turn, and a later
    post here with the same key never runs the loop: a finished exchange
    replays (200, or the stored failure's status and body), one that never
    finished is a 409, and different words under the key are a 422. Checked
    under the run lock, so a retry racing the original waits for its answer.

    **Structural confirmation** (#503, delegate-api "Confirmation"). On a
    delegate run (a recognized ``X-Agent-Actor``) a destructive call
    (``confirmation.GATED_TOOLS``) never runs unasked: the run stops
    ``confirm_required`` and ``assistant_message.confirm_required`` carries a
    single-use token with the code-built question and preview. A message with
    ``Confirm-Token`` runs exactly that call once, with no model turn; a token
    that can't be honoured is a 409 naming why (``unknown`` / ``expired`` /
    ``used`` / ``stale``). The next message without the token voids the
    pending question unless its run re-issues the identical call. PCC's own
    chat is never gated.
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
        if idempotency_key is not None:
            seen = conversations_service.find_exchange(db, conversation.id, idempotency_key)
            if seen is not None:
                return _replayed(data.content, *seen)
        if confirm_token is not None:
            # Release the write lock before the token's state check reads the
            # rows on its own session; nothing is written yet, and the run lock
            # still holds the thread.
            db.commit()
            try:
                # The path's id, not ``conversation.id``: reading the expired
                # row would open a transaction again.
                pending = confirmation.redeem(confirm_token, conversation_id)
            except confirmation.ConfirmRefused as refused:
                return _confirm_refused(refused.kind)
            return _run_confirmed(
                db, conversation, data.content, idempotency_key, pending, loop, actor
            )
        gate = None
        if actor in DELEGATE_ACTORS:
            gate = confirmation.Gate(
                conversation.id, answered=confirmation.take_for_thread(conversation.id)
            )
        history = conversations_service.history_for_loop(db, conversation.id)
        user_message = conversations_service.append_user_message(
            db, conversation, data.content, idempotency_key
        )
        db.commit()

        try:
            # Registered inside the run lock, so a Stop always targets the run
            # actually in flight — never a queued or future one (run_cancel).
            # The conversation id rides the log context into the run, which
            # stamps it on the run's log lines and its trace record (#400).
            with (
                active_run(conversation_id) as cancel,
                structlog.contextvars.bound_contextvars(conversation_id=conversation_id),
            ):
                run = loop.run(
                    data.content,
                    history=history,
                    actor=actor,
                    deadline=deadline,
                    cancel=cancel,
                    gate=gate,
                )
        except AgentRunFailed as exc:
            logger.error(
                "agent_run_failed",
                conversation_id=conversation_id,
                stop_reason=exc.result.stop_reason,
                error=str(exc),
            )
            failed = conversations_service.append_assistant_message(
                db, conversation, exc.result
            )
            db.commit()
            return _failure_response(exc.http_status, str(exc), failed)

        assistant_message = conversations_service.append_assistant_message(
            db, conversation, run
        )
        db.commit()
        if run.confirm is not None:
            run.confirm.message_id = assistant_message.id
        return MessageExchange(
            user_message=MessageRead.model_validate(user_message),
            assistant_message=_assistant_read(assistant_message, run.confirm),
        )


def _run_confirmed(
    db: Session,
    conversation: Conversation,
    content: str,
    idempotency_key: str | None,
    pending: confirmation.Pending,
    loop: AgentLoop,
    actor: str,
) -> MessageExchange:
    """The answer to a redeemed ``Confirm-Token``: exactly the armed call,
    once, no model turn. The reply is the code-built record of that call."""
    user_message = conversations_service.append_user_message(
        db, conversation, content, idempotency_key
    )
    # Commit first: the tool takes its own write session (see above).
    db.commit()
    record = loop.dispatch_confirmed(pending, actor)
    run = AgentRunResult(
        reply=run_record.render([record]),
        stop_reason="completed",
        tool_calls=[record],
        iterations=0,
        messages=[],
    )
    assistant_message = conversations_service.append_assistant_message(db, conversation, run)
    db.commit()
    logger.info(
        "agent_confirmed_call",
        conversation_id=conversation.id,
        tool=pending.tool,
        ok=record.error is None,
    )
    return MessageExchange(
        user_message=MessageRead.model_validate(user_message),
        assistant_message=MessageRead.model_validate(assistant_message),
    )


@router.post(
    "/conversations/{conversation_id}/cancel", response_model=RunCancelRead
)
def cancel_run(
    conversation_id: EntityId, reader: Session = Depends(get_db)
) -> RunCancelRead:
    """Ask this conversation's in-flight run to stop; **404** if no such thread.

    Idempotent and never blocks: it only sets the running run's cancel event
    (``run_cancel``) and returns. The run stops at its next boundary — before
    the next provider call or tool dispatch, never mid-tool, and a model call
    already in flight finishes first — and its own pending ``POST …/messages``
    returns 200 with the ``cancelled`` turn. With nothing running the answer is
    ``cancel_requested: false`` and nothing is remembered. Read-only on the DB:
    it writes no row, so it takes no write session and cannot contend with the
    run it is stopping.
    """
    _get_or_404(reader, conversation_id)
    return RunCancelRead(cancel_requested=request_cancel(conversation_id))
