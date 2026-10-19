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

Process-local by design, matching ``conversation_locks`` and ``rate_limit``:
the deployment runs one uvicorn worker.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

logger = structlog.get_logger(__name__)

# conversation_id -> the cancel event of its in-flight run. Guarded by
# ``_LOCK``. At most one run per conversation holds the run lock, so at most
# one event is registered per conversation.
_LOCK = threading.Lock()
_ACTIVE: dict[int, threading.Event] = {}


@contextmanager
def active_run(conversation_id: int) -> Iterator[threading.Event]:
    """Register a fresh cancel event for this run; unregister it on exit.

    Must be entered while holding ``conversation_run_lock`` so that "the
    registered event" and "the run in flight" are the same thing.
    """
    event = threading.Event()
    with _LOCK:
        _ACTIVE[conversation_id] = event
    try:
        yield event
    finally:
        with _LOCK:
            # Only drop our own entry — never a successor's.
            if _ACTIVE.get(conversation_id) is event:
                del _ACTIVE[conversation_id]


def request_cancel(conversation_id: int) -> bool:
    """Ask the conversation's in-flight run to stop. True if one was running.

    Idempotent: asking twice while a run is in flight is the same as asking
    once. With nothing running it does nothing and returns False — there is no
    flag left behind for a later run to trip over.
    """
    with _LOCK:
        event = _ACTIVE.get(conversation_id)
        if event is None:
            return False
        event.set()
    logger.info("agent_run_cancel_requested", conversation_id=conversation_id)
    return True


def _reset() -> None:
    """Drop all registrations. For tests only — keeps cases independent."""
    with _LOCK:
        _ACTIVE.clear()
