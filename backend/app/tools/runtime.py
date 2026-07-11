"""Per-tool-call plumbing shared by every tool consumer: session, actor, request ID.

Every registry tool body runs inside :func:`tool_session`, which mirrors what
the HTTP stack assembles from middleware + dependencies: a request ID bound to
the logger, the actor bound for ``activity_events`` attribution, and a session
whose transaction commits on success and rolls back on any error.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import structlog
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services import activity

logger = structlog.get_logger(__name__)

# Stamped into activity_events.actor for writes made through the MCP server.
# NULL remains "the user"; the in-app loop stamps its own value (LOOP_ACTOR in
# app/ai/loop.py).
MCP_ACTOR = "agent:mcp"

# Which agent identity the current tool call writes as. Defaults to the MCP
# server: FastMCP dispatches straight into the tool bodies with no seam to
# pass an actor through, while the in-app loop dispatches via
# ``registry.call_tool``, which overrides this per call.
current_tool_actor: ContextVar[str] = ContextVar("current_tool_actor", default=MCP_ACTOR)

# Tests swap this for a factory bound to their in-memory engine; the alias
# exists so they never have to touch app.db.session's real engine.
session_factory = SessionLocal


@contextmanager
def tool_session(tool_name: str) -> Iterator[Session]:
    """One tool call = one request: logging context, actor, transaction.

    Commits when the tool body finishes cleanly (a no-op for pure reads) and
    rolls back on any exception — a failed tool call never leaves a partial
    write behind. A request ID is minted only when the caller hasn't bound one
    already: the MCP server hasn't (one fresh ID per tool call, as before),
    while the agent loop binds one per run so all its tool calls share it.
    """
    bindings: dict[str, str] = {"tool": tool_name}
    if "request_id" not in structlog.contextvars.get_contextvars():
        bindings["request_id"] = uuid.uuid4().hex[:8]
    actor_token = activity.current_actor.set(current_tool_actor.get())
    db = session_factory()
    try:
        with structlog.contextvars.bound_contextvars(**bindings):
            yield db
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        activity.current_actor.reset(actor_token)
