"""Stop requests for in-flight agent runs (#324).

``post_message`` registers one fresh :class:`threading.Event` per run, *inside*
the conversation's run lock, and hands it to the loop; ``POST
/agent/conversations/{id}/cancel`` sets whichever event is registered for that
conversation right now. The loop checks it before every provider call and tool
dispatch (see ``app/ai/loop.py``).

Why a per-run event rather than a per-conversation flag: a flag would outlive
the run it was meant for. A Stop that lands just after a run finished — or
between two queued runs — would sit there and cancel the *next* run the user
starts. Here there is nothing to leak: when no run is registered, a cancel is a
no-op that reports so, and every run starts with an event nobody has set.

The same registry answers "is a run in flight, and since when?" for the
conversation detail (#358), so a page that did not start the run — a reload, a
second tab — can still show it and stop it.

Process-local by design, matching ``conversation_locks`` and ``rate_limit``:
the deployment runs one uvicorn worker.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _Run:
    """One registered run: its cancel event and when it was registered."""

    cancel: threading.Event = field(default_factory=threading.Event)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# conversation_id -> its in-flight run. Guarded by ``_LOCK``. At most one run
# per conversation holds the run lock, so at most one run is registered per
# conversation.
_LOCK = threading.Lock()
_ACTIVE: dict[int, _Run] = {}


@contextmanager
def active_run(conversation_id: int) -> Iterator[threading.Event]:
    """Register a fresh cancel event for this run; unregister it on exit.

    Must be entered while holding ``conversation_run_lock`` so that "the
    registered event" and "the run in flight" are the same thing.
    """
    run = _Run()
    with _LOCK:
        _ACTIVE[conversation_id] = run
    try:
        yield run.cancel
    finally:
        with _LOCK:
            # Only drop our own entry — never a successor's.
            if _ACTIVE.get(conversation_id) is run:
                del _ACTIVE[conversation_id]


def run_started_at(conversation_id: int) -> datetime | None:
    """When the conversation's in-flight run started (UTC), or None if idle."""
    with _LOCK:
        run = _ACTIVE.get(conversation_id)
    return None if run is None else run.started_at


def request_cancel(conversation_id: int) -> bool:
    """Ask the conversation's in-flight run to stop. True if one was running.

    Idempotent: asking twice while a run is in flight is the same as asking
    once. With nothing running it does nothing and returns False — there is no
    flag left behind for a later run to trip over.
    """
    with _LOCK:
        run = _ACTIVE.get(conversation_id)
        if run is None:
            return False
        run.cancel.set()
    logger.info("agent_run_cancel_requested", conversation_id=conversation_id)
    return True


def _reset() -> None:
    """Drop all registrations. For tests only — keeps cases independent."""
    with _LOCK:
        _ACTIVE.clear()
