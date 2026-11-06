"""Structural confirmation of destructive writes over the delegate API (#503).

agent-standard ``delegate-api.md`` "Confirmation" and ``STANDARD.md`` §1/§3:
the app that owns a write owns its confirmation. On a delegate run a gated
call (:data:`GATED_TOOLS`) without a valid confirmation never reaches the
service layer. The loop arms a :class:`Pending` here, records the call "not
run", and stops the run ``confirm_required`` with a question and preview built
by code from the live rows, never by the model.

Two ways to answer, both only on the thread that asked:

- **The token.** A message carrying ``Confirm-Token`` runs exactly the armed
  call, once, with no model turn (``routes_agent``).
- **The app's own yes** (the v1 path, chess's answer reader). The next
  message's run may re-issue the identical call, and :class:`Gate` lets it
  through.

Anything else voids it: the next message on the thread without the token
takes the pending confirmation off the thread, whatever the run then does.

A token is bound to the thread, the tool, its canonical arguments, and the
state it previewed (the row's ``updated_at``), and expires after
:data:`TOKEN_TTL_SECONDS`. Tokens live in this process only: a restart
forgets them, which a client meets as ``unknown`` and asks again. PCC's own
chat (the in-app actor) is never gated: there, trash is the undo.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select

from app.db.models import Task
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.tools.runtime import tool_session

# The writes a delegate must confirm: each one trashes a row together with
# what hangs off it (a project's tasks, a task's subtasks).
GATED_TOOLS = frozenset({"trash_task", "trash_project"})

# How long a question stays answerable (the contract's default).
TOKEN_TTL_SECONDS = 600

# The ``reason`` a gated call's record entry carries: it never ran.
NOT_RUN_REASON = "not run: needs confirmation"

Refusal = Literal["unknown", "expired", "used", "stale"]


class ConfirmRefused(Exception):
    """A ``Confirm-Token`` that can't be honoured; nothing runs (a 409)."""

    def __init__(self, kind: Refusal) -> None:
        super().__init__(kind)
        self.kind: Refusal = kind


@dataclass
class Pending:
    """One armed confirmation: exactly one call, on one thread."""

    token: str
    conversation_id: int
    tool: str
    arguments: dict[str, Any]
    state: str
    question: str
    preview: dict[str, Any]
    expires_at: float
    used: bool = False
    # The assistant turn that asked, so a replayed exchange still carries it.
    message_id: int | None = None
    _key: str = field(init=False)

    def __post_init__(self) -> None:
        self._key = _call_key(self.tool, self.arguments)

    def matches(self, tool: str, arguments: dict[str, Any]) -> bool:
        return self._key == _call_key(tool, arguments)

    def wire(self) -> dict[str, Any]:
        """``assistant_message.confirm_required`` as the contract shows it."""
        expires = datetime.fromtimestamp(self.expires_at, UTC)
        return {
            "token": self.token,
            "tool": self.tool,
            "question": self.question,
            "preview": self.preview,
            "expires_at": expires.isoformat(),
        }


_lock = threading.Lock()
_by_token: dict[str, Pending] = {}
_by_thread: dict[int, str] = {}


def _call_key(tool: str, arguments: dict[str, Any]) -> str:
    return json.dumps([tool, arguments], sort_keys=True, separators=(",", ":"), default=str)


def _expired(pending: Pending) -> bool:
    return time.time() >= pending.expires_at


def _sweep() -> None:
    """Forget tokens past their window (called under the lock)."""
    for token in [t for t, p in _by_token.items() if _expired(p)]:
        pending = _by_token.pop(token)
        if _by_thread.get(pending.conversation_id) == token:
            del _by_thread[pending.conversation_id]


class Gate:
    """What one delegate run may and may not dispatch (handed to the loop)."""

    def __init__(self, conversation_id: int, answered: Pending | None = None) -> None:
        self._conversation_id = conversation_id
        # The confirmation this message may answer with a re-issued call.
        self._answered = answered
        self.armed: Pending | None = None

    def gated(self, tool: str) -> bool:
        return tool in GATED_TOOLS

    def allows(self, tool: str, arguments: dict[str, Any]) -> bool:
        """Whether this call is the identical one the thread was asked about,
        with the state it previewed. It may run once."""
        answered = self._answered
        if answered is None or not answered.matches(tool, arguments):
            return False
        self._answered = None
        if _expired(answered) or _fingerprint(tool, arguments) != answered.state:
            return False
        with _lock:
            answered.used = True
        return True

    def arm(self, tool: str, arguments: dict[str, Any]) -> Pending | None:
        """Arm a confirmation for this call, or ``None`` if its row isn't there
        (the call then runs, and the service layer refuses it)."""
        built = _question(tool, arguments)
        if built is None:
            return None
        state, question, preview = built
        pending = Pending(
            token=secrets.token_urlsafe(18),
            conversation_id=self._conversation_id,
            tool=tool,
            arguments=dict(arguments),
            state=state,
            question=question,
            preview=preview,
            expires_at=time.time() + TOKEN_TTL_SECONDS,
        )
        with _lock:
            _sweep()
            previous = _by_thread.pop(self._conversation_id, None)
            if previous is not None:
                _by_token.pop(previous, None)
            _by_token[pending.token] = pending
            _by_thread[self._conversation_id] = pending.token
        self.armed = pending
        return pending


def take_for_thread(conversation_id: int) -> Pending | None:
    """The next message without a token settles the thread's confirmation:
    it comes off the thread now, and only a re-issued identical call in this
    message's run can still use it (:meth:`Gate.allows`)."""
    with _lock:
        _sweep()
        token = _by_thread.pop(conversation_id, None)
        if token is None:
            return None
        pending = _by_token.get(token)
        if pending is not None:
            # The token itself is spent as well: the thread moved on.
            del _by_token[token]
        return pending if pending is not None and not pending.used else None


def redeem(token: str, conversation_id: int) -> Pending:
    """Validate a ``Confirm-Token`` and spend it, or raise :class:`ConfirmRefused`."""
    with _lock:
        pending = _by_token.get(token)
        if pending is None or pending.conversation_id != conversation_id:
            raise ConfirmRefused("unknown")
        if pending.used:
            raise ConfirmRefused("used")
        if _expired(pending):
            raise ConfirmRefused("expired")
        pending.used = True
        if _by_thread.get(conversation_id) == token:
            del _by_thread[conversation_id]
    if _fingerprint(pending.tool, pending.arguments) != pending.state:
        raise ConfirmRefused("stale")
    return pending


def for_message(message_id: int) -> Pending | None:
    """The live confirmation an assistant turn asked, for a replayed exchange."""
    with _lock:
        for pending in _by_token.values():
            if pending.message_id == message_id and not pending.used and not _expired(pending):
                return pending
    return None


def reset() -> None:
    """Forget every token (tests)."""
    with _lock:
        _by_token.clear()
        _by_thread.clear()


def _fingerprint(tool: str, arguments: dict[str, Any]) -> str | None:
    built = _question(tool, arguments)
    return None if built is None else built[0]


def _question(tool: str, arguments: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """``(state, question, preview)`` for a gated call, from the live rows."""
    with tool_session(f"confirm:{tool}", write=False) as db:
        if tool == "trash_project":
            project = projects_service.get_project(db, int(arguments.get("project_id", 0)))
            if project is None:
                return None
            tasks = int(
                db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.project_id == project.id, Task.deleted_at.is_(None))
                ).scalar_one()
            )
            noun = "task" if tasks == 1 else "tasks"
            question = (
                f'Trash project "{project.name}" and its {tasks} {noun}?'
                if tasks
                else f'Trash project "{project.name}"?'
            )
            ref = {"app": "tasks", "kind": "project", "id": str(project.id), "title": project.name}
            return (
                f"project:{project.id}:{project.updated_at.isoformat()}:{tasks}",
                question,
                {"count": 1 + tasks, "touched": [ref]},
            )
        if tool == "trash_task":
            task = tasks_service.get_task(db, int(arguments.get("task_id", 0)))
            if task is None:
                return None
            subtasks = int(
                db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.parent_task_id == task.id, Task.deleted_at.is_(None))
                ).scalar_one()
            )
            noun = "subtask" if subtasks == 1 else "subtasks"
            question = (
                f'Trash task "{task.title}" and its {subtasks} {noun}?'
                if subtasks
                else f'Trash task "{task.title}"?'
            )
            ref = {"app": "tasks", "kind": "task", "id": str(task.id), "title": task.title}
            return (
                f"task:{task.id}:{task.updated_at.isoformat()}:{subtasks}",
                question,
                {"count": 1 + subtasks, "touched": [ref]},
            )
    return None
