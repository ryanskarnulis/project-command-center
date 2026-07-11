"""Per-tool-call plumbing for the MCP server: session, actor, request ID.

Every tool body runs inside :func:`tool_session`, which mirrors what the HTTP
stack assembles from middleware + dependencies: a request ID bound to the
logger, the actor bound for ``activity_events`` attribution, and a session
whose transaction commits on success and rolls back on any error.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import structlog
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services import activity

logger = structlog.get_logger(__name__)

# Stamped into activity_events.actor for every write made through this server.
# NULL remains "the user"; a future in-app agent loop binds its own value.
MCP_ACTOR = "agent:mcp"

# Tests swap this for a factory bound to their in-memory engine; the alias
# exists so they never have to touch app.db.session's real engine.
session_factory = SessionLocal


@contextmanager
def tool_session(tool_name: str) -> Iterator[Session]:
    """One tool call = one request: logging context, actor, transaction.

    Commits when the tool body finishes cleanly (a no-op for pure reads) and
    rolls back on any exception — a failed tool call never leaves a partial
    write behind.
    """
    structlog.contextvars.bind_contextvars(
        request_id=uuid.uuid4().hex[:8], tool=tool_name
    )
    actor_token = activity.current_actor.set(MCP_ACTOR)
    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        activity.current_actor.reset(actor_token)
        structlog.contextvars.unbind_contextvars("request_id", "tool")
