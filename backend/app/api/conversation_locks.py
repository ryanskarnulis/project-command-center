"""Per-conversation run serialization for the agent message endpoint.

Two messages posted to the same conversation must not run concurrently: they
would read overlapping history and persist their replies out of order. This
module hands out one lock per conversation so a second run waits for the first
to finish (and then sees its turns in history), giving up with **409** if it
waits past the run's time budget.

Process-local by design, matching ``app/api/rate_limit.py``: the deployment runs
a single uvicorn worker, so an in-process lock is sufficient. A multi-worker
deploy would need a DB-level lock behind this same interface.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import structlog
from fastapi import HTTPException, status

logger = structlog.get_logger(__name__)


@dataclass
class _Entry:
    """One conversation's lock plus how many requests hold or wait on it."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    refcount: int = 0


# conversation_id -> its lock entry. Guarded by ``_REGISTRY_LOCK``; an entry is
# dropped once no request references it, so the map can't grow without bound.
_REGISTRY_LOCK = threading.Lock()
_ENTRIES: dict[int, _Entry] = {}


def _checkout(conversation_id: int) -> threading.Lock:
    with _REGISTRY_LOCK:
        entry = _ENTRIES.get(conversation_id)
        if entry is None:
            entry = _Entry()
            _ENTRIES[conversation_id] = entry
        entry.refcount += 1
        return entry.lock


def _checkin(conversation_id: int) -> None:
    with _REGISTRY_LOCK:
        entry = _ENTRIES.get(conversation_id)
        if entry is None:
            return
        entry.refcount -= 1
        if entry.refcount <= 0:
            del _ENTRIES[conversation_id]


def _reset() -> None:
    """Drop all locks. For tests only — keeps cases independent."""
    with _REGISTRY_LOCK:
        _ENTRIES.clear()


@contextmanager
def conversation_run_lock(
    conversation_id: int, *, wait_seconds: float
) -> Iterator[None]:
    """Hold this conversation's run lock, or raise 409 if the wait runs out.

    A concurrent run on the same conversation blocks here until the holder
    finishes; if that takes longer than ``wait_seconds`` the request is rejected
    with 409 rather than piling up behind a stalled run.
    """
    lock = _checkout(conversation_id)
    if not lock.acquire(timeout=wait_seconds):
        _checkin(conversation_id)
        logger.warning(
            "conversation_run_busy",
            conversation_id=conversation_id,
            waited_seconds=wait_seconds,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a run is already in progress for this conversation",
        )
    try:
        yield
    finally:
        lock.release()
        _checkin(conversation_id)
