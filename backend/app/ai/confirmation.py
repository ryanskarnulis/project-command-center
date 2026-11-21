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
  message's run may re-issue the identical calls, and :class:`Gate` lets
  them through.

Every gated call of one model turn gets its own question and token (#545):
"delete the test tasks" is five ``trash_task`` calls and five questions, each
answered on its own. Answering one with its token leaves the others open.

Anything else voids them: the next message on the thread without a token
takes every pending confirmation off the thread, whatever the run then does.

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


Call = tuple[str, dict[str, Any]]


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
        """One confirmation as the contract shows it."""
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
# A thread's open confirmations, in the order they were asked.
_by_thread: dict[int, list[str]] = {}


def _call_key(tool: str, arguments: dict[str, Any]) -> str:
    return json.dumps([tool, arguments], sort_keys=True, separators=(",", ":"), default=str)


def _expired(pending: Pending) -> bool:
    return time.time() >= pending.expires_at


def _sweep() -> None:
    """Forget tokens past their window (called under the lock)."""
    for token in [t for t, p in _by_token.items() if _expired(p)]:
        _forget(_by_token.pop(token))


def _forget(pending: Pending) -> None:
    """Take a token off its thread's open list (called under the lock)."""
    tokens = _by_thread.get(pending.conversation_id)
    if tokens is None or pending.token not in tokens:
        return
    tokens.remove(pending.token)
    if not tokens:
        del _by_thread[pending.conversation_id]


class Gate:
    """What one delegate run may and may not dispatch (handed to the loop)."""

    def __init__(self, conversation_id: int, answered: list[Pending] | None = None) -> None:
        self._conversation_id = conversation_id
        # The confirmations this message may answer with re-issued calls.
        self._answered = list(answered or [])
        self.armed: list[Pending] = []

    def gated(self, tool: str) -> bool:
        return tool in GATED_TOOLS

    def allows(self, tool: str, arguments: dict[str, Any]) -> bool:
        """Whether this call is identical to one the thread was asked about,
        with the state it previewed. Each call asked about may run once."""
        answered = next((p for p in self._answered if p.matches(tool, arguments)), None)
        if answered is None:
            return False
        self._answered.remove(answered)
        if _expired(answered) or _fingerprint(tool, arguments) != answered.state:
            return False
        with _lock:
            answered.used = True
        return True

    def arm(self, calls: list[Call]) -> list[Pending]:
        """Arm one confirmation per gated call of a model turn, in order.
        Empty if the first one's row isn't there (that call then runs, and
        the service layer refuses it). A later call whose row isn't there, or
        that repeats an earlier one, is left out."""
        armed: list[Pending] = []
        seen: set[str] = set()
        expires_at = time.time() + TOKEN_TTL_SECONDS
        for tool, arguments in calls:
            key = _call_key(tool, arguments)
            asked = None if key in seen else _asked(tool, arguments)
            if asked is None:
                if not armed:
                    return []
                continue
            seen.add(key)
            armed.append(
                Pending(
                    token=secrets.token_urlsafe(18),
                    conversation_id=self._conversation_id,
                    tool=tool,
                    arguments=dict(arguments),
                    state=asked.state,
                    question=f"Trash {asked.phrase()}?",
                    preview={"count": asked.count, "touched": asked.touched},
                    expires_at=expires_at,
                )
            )
        with _lock:
            _sweep()
            for previous in _by_thread.pop(self._conversation_id, []):
                _by_token.pop(previous, None)
            for pending in armed:
                _by_token[pending.token] = pending
            _by_thread[self._conversation_id] = [pending.token for pending in armed]
        self.armed = armed
        return armed


def take_for_thread(conversation_id: int) -> list[Pending]:
    """The next message without a token settles the thread's confirmations:
    they come off the thread now, and only re-issued identical calls in this
    message's run can still use them (:meth:`Gate.allows`)."""
    with _lock:
        _sweep()
        taken = []
        for token in _by_thread.pop(conversation_id, []):
            # The token itself is spent as well: the thread moved on.
            pending = _by_token.pop(token, None)
            if pending is not None and not pending.used:
                taken.append(pending)
        return taken


def redeem(token: str, conversation_id: int) -> Pending:
    """Validate a ``Confirm-Token`` and spend it, or raise
    :class:`ConfirmRefused`. The thread's other confirmations stay open."""
    with _lock:
        pending = _by_token.get(token)
        if pending is None or pending.conversation_id != conversation_id:
            raise ConfirmRefused("unknown")
        if pending.used:
            raise ConfirmRefused("used")
        if _expired(pending):
            raise ConfirmRefused("expired")
        pending.used = True
        _forget(pending)
    if _fingerprint(pending.tool, pending.arguments) != pending.state:
        raise ConfirmRefused("stale")
    return pending


def for_message(message_id: int) -> list[Pending]:
    """The live confirmations an assistant turn asked, for a replayed exchange."""
    with _lock:
        return [
            pending
            for pending in _by_token.values()
            if pending.message_id == message_id and not pending.used and not _expired(pending)
        ]


def wire(pendings: list[Pending]) -> dict[str, Any]:
    """What an assistant turn carries for the confirmations it asked:
    ``confirm_required`` is the first, ``confirms_required`` is all of them."""
    if not pendings:
        return {}
    return {
        "confirm_required": pendings[0].wire(),
        "confirms_required": [pending.wire() for pending in pendings],
    }


def reset() -> None:
    """Forget every token (tests)."""
    with _lock:
        _by_token.clear()
        _by_thread.clear()


def _fingerprint(tool: str, arguments: dict[str, Any]) -> str | None:
    built = _asked(tool, arguments)
    return None if built is None else built.state


@dataclass(frozen=True)
class _Asked:
    """One gated call as the question shows it, read from the live rows."""

    state: str
    kind: str  # "task" | "project"
    title: str
    # What goes with it: a task's subtasks, a project's tasks.
    inside: int
    touched: list[dict[str, Any]]

    @property
    def count(self) -> int:
        return 1 + self.inside

    def phrase(self) -> str:
        if not self.inside:
            return f'{self.kind} "{self.title}"'
        noun = "subtask" if self.kind == "task" else "task"
        return f'{self.kind} "{self.title}" and its {self.inside} {noun}{"" if self.inside == 1 else "s"}'


def _asked(tool: str, arguments: dict[str, Any]) -> _Asked | None:
    """A gated call's row as the question shows it, or ``None`` if it's gone."""
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
            ref = {"app": "tasks", "kind": "project", "id": str(project.id), "title": project.name}
            return _Asked(
                state=f"project:{project.id}:{project.updated_at.isoformat()}:{tasks}",
                kind="project",
                title=project.name,
                inside=tasks,
                touched=[ref],
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
            ref = {"app": "tasks", "kind": "task", "id": str(task.id), "title": task.title}
            return _Asked(
                state=f"task:{task.id}:{task.updated_at.isoformat()}:{subtasks}",
                kind="task",
                title=task.title,
                inside=subtasks,
                touched=[ref],
            )
    return None
