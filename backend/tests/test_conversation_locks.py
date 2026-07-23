"""Per-conversation run serialization (app/api/conversation_locks.py).

Deterministic, no threads: a non-reentrant lock re-acquired for the same
conversation while already held is exactly the "second run arrived" case.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import conversation_locks
from app.api.conversation_locks import conversation_run_lock


def test_second_run_on_same_conversation_is_rejected_while_first_holds() -> None:
    with conversation_run_lock(1, wait_seconds=5.0):
        with pytest.raises(HTTPException) as exc_info:
            with conversation_run_lock(1, wait_seconds=0.01):
                pass  # pragma: no cover — the acquire fails before the body
        assert exc_info.value.status_code == 409

    # Once the first run releases, the conversation is runnable again.
    with conversation_run_lock(1, wait_seconds=0.01):
        pass


def test_different_conversations_do_not_block_each_other() -> None:
    with conversation_run_lock(1, wait_seconds=0.01):
        with conversation_run_lock(2, wait_seconds=0.01):
            pass


def test_lock_entry_is_dropped_once_unused() -> None:
    with conversation_run_lock(7, wait_seconds=0.01):
        assert 7 in conversation_locks._ENTRIES
    assert 7 not in conversation_locks._ENTRIES
