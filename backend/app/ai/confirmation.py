"""Structural confirmation of destructive writes over the delegate API (#503).

agent-standard ``delegate-api.md`` "Confirmation" and ``STANDARD.md`` §1/§3:
the app that owns a write owns its confirmation. On a delegate run a gated
call (:data:`GATED_TOOLS`) without a valid confirmation never reaches the
service layer. The loop arms a :class:`Pending` here, records the call "not
run", and stops the run ``confirm_required`` with a question and preview built
by code from the live rows, never by the model.

A question is a card, and only its tap approves it (conductor#251). The one
way to answer is the token: a message carrying ``Confirm-Token`` runs exactly
the armed call, once, with no model turn (``routes_agent``). No words approve
one: a "yes" whose run re-issues the call is asked again.

Every gated call of one model turn gets its own question and token (#545):
"delete the test tasks" is five ``trash_task`` calls and five questions, each
answered on its own. Other messages on the thread leave them open. The same
call asked again replaces its old question, so a call has one open token.

A token is bound to the thread, the tool, its canonical arguments, and the
state it previewed (the row's ``updated_at``), and lives as long as the card:
:data:`TOKEN_TTL_SECONDS`, the caller's conversation window. Redeeming one
re-checks the row: changed, finished or gone since it was asked about is
``stale``, said in words, and nothing runs. Tokens live in this process only:
a restart forgets them, which a client meets as ``unknown`` and asks again.
PCC's own chat (the in-app actor) is never gated, so it has nothing to
approve: there, trash is the undo.
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

from app.db.models import Task, TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.tools.runtime import tool_session

# The writes a delegate must confirm: each one trashes a row together with
# what hangs off it (a project's tasks, a task's subtasks).
GATED_TOOLS = frozenset({"trash_task", "trash_project"})

# How long a question stays answerable: as long as its card, which lapses with
# the caller's conversation window (conductor's quiet gap, 3 h).
TOKEN_TTL_SECONDS = 3 * 60 * 60

# The ``reason`` a gated call's record entry carries: it never ran.
NOT_RUN_REASON = "not run: needs confirmation"

Refusal = Literal["unknown", "expired", "used", "stale"]


class ConfirmRefused(Exception):
    """A ``Confirm-Token`` that can't be honoured; nothing runs (a 409)."""

    def __init__(self, kind: Refusal, detail: str | None = None) -> None:
        super().__init__(kind)
        self.kind: Refusal = kind
        # What changed, in words fit to show the person (``stale`` only).
        self.detail = detail


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
    # Whether the row was already finished when it was asked about.
    done: bool = False
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

    def __init__(self, conversation_id: int) -> None:
        self._conversation_id = conversation_id
        self.armed: list[Pending] = []

    def gated(self, tool: str) -> bool:
        return tool in GATED_TOOLS

    def arm(self, calls: list[Call]) -> list[Pending]:
        """Arm one confirmation per gated call of a model turn, in order.
        Empty if the first one's row isn't there (that call then runs, and
        the service layer refuses it). A later call whose row isn't there, or
        that repeats an earlier one, is left out. A call the thread was
        already asked about is asked again: the new question replaces it."""
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
                    done=asked.done,
                )
            )
        with _lock:
            _sweep()
            open_tokens = _by_thread.setdefault(self._conversation_id, [])
            for token in list(open_tokens):
                if _by_token[token]._key in seen:
                    open_tokens.remove(token)
                    del _by_token[token]
            for pending in armed:
                _by_token[pending.token] = pending
                open_tokens.append(pending.token)
        self.armed = armed
        return armed


def redeem(token: str, conversation_id: int) -> Pending:
    """Validate a ``Confirm-Token`` and spend it, or raise
    :class:`ConfirmRefused`. The thread's other confirmations stay open. The
    row is read again: one changed, finished or gone since is ``stale``."""
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
    now = _asked(pending.tool, pending.arguments)
    if now is None or now.state != pending.state:
        raise ConfirmRefused("stale", _what_changed(pending, now))
    return pending


def _what_changed(pending: Pending, now: _Asked | None) -> str:
    """Why a stale token ran nothing, in words for the person."""
    thing = pending.question.removeprefix("Trash ").removesuffix("?")
    if now is None:
        what = "is no longer there"
    elif now.done and not pending.done:
        what = "was finished since you were asked"
    else:
        what = "changed since you were asked"
    return f"{thing} {what}, so nothing was trashed"


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


@dataclass(frozen=True)
class _Asked:
    """One gated call as the question shows it, read from the live rows."""

    state: str
    kind: str  # "task" | "project"
    title: str
    # What goes with it: a task's subtasks, a project's tasks.
    inside: int
    touched: list[dict[str, Any]]
    # A task already finished (a project has no such state).
    done: bool = False

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
                done=task.workflow_status == TaskWorkflowStatus.done,
            )
    return None
