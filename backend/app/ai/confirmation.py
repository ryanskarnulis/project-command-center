"""Structural confirmation of destructive writes over the delegate API (#503).

agent-standard ``delegate-api.md`` "Confirmation" and ``STANDARD.md`` §1/§3:
the app that owns a write owns its confirmation. On a delegate run a gated
call (:data:`GATED_TOOLS`) without a valid confirmation never reaches the
service layer. The loop arms a :class:`Pending` here, records the call "not
run", and stops the run ``confirm_required`` with a question and preview built
by code from the live rows, never by the model.

Two ways to answer, both only on the thread that asked:

- **The token.** A message carrying ``Confirm-Token`` runs exactly what was
  armed, once, with no model turn (``routes_agent``).
- **The app's own yes** (the v1 path, chess's answer reader). The next
  message's run may re-issue the identical calls, and :class:`Gate` lets
  them through.

One question covers one model turn's gated calls (#545): "delete the test
tasks" is five ``trash_task`` calls, and the person is asked once, about all
five by name. A yes runs every one of them.

Anything else voids it: the next message on the thread without the token
takes the pending confirmation off the thread, whatever the run then does.

A token is bound to the thread, each call's tool and canonical arguments, and
the state it previewed (the row's ``updated_at``), and expires after
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

# The contract's cap on the entity refs a preview lists.
PREVIEW_REFS = 20

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
    """One armed confirmation on one thread: the gated calls of one model
    turn, asked about together. ``tool`` and ``arguments`` are the first."""

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
    # The calls asked about after the first, in the order the model made them.
    more: list[Call] = field(default_factory=list)
    # Each call's previewed state by call key; a re-issued call spends its key.
    _states: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        states = self.state.split(_STATE_SEPARATOR)
        self._states = {
            _call_key(tool, arguments): state
            for (tool, arguments), state in zip(self.calls, states, strict=True)
        }

    @property
    def calls(self) -> list[Call]:
        return [(self.tool, self.arguments), *self.more]

    def covers(self, tool: str, arguments: dict[str, Any]) -> bool:
        return _call_key(tool, arguments) in self._states

    def spend(self, tool: str, arguments: dict[str, Any]) -> str | None:
        """Take one call off the confirmation: the state it previewed, or
        ``None`` if it is not (or no longer) one of the calls asked about."""
        return self._states.pop(_call_key(tool, arguments), None)

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


# Joins the calls' states into ``Pending.state`` (never part of one state).
_STATE_SEPARATOR = "\n"

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
        """Whether this call is identical to one the thread was asked about,
        with the state it previewed. Each call asked about may run once."""
        answered = self._answered
        if answered is None:
            return False
        state = answered.spend(tool, arguments)
        if state is None:
            # Not one of the calls asked about: the yes covers nothing more.
            self._answered = None
            return False
        if _expired(answered) or _fingerprint(tool, arguments) != state:
            self._answered = None
            return False
        with _lock:
            answered.used = True
        return True

    def arm(self, calls: list[Call]) -> Pending | None:
        """Arm one confirmation for a model turn's gated calls, or ``None`` if
        the first one's row isn't there (that call then runs, and the service
        layer refuses it). A later call whose row isn't there, or that repeats
        an earlier one, is left out of the question."""
        asked: list[tuple[Call, _Asked]] = []
        seen: set[str] = set()
        for tool, arguments in calls:
            key = _call_key(tool, arguments)
            built = None if key in seen else _asked(tool, arguments)
            if built is None:
                if not asked:
                    return None
                continue
            seen.add(key)
            asked.append(((tool, dict(arguments)), built))
        (tool, arguments), _first = asked[0]
        touched = [ref for _call, item in asked for ref in item.touched]
        pending = Pending(
            token=secrets.token_urlsafe(18),
            conversation_id=self._conversation_id,
            tool=tool,
            arguments=arguments,
            state=_STATE_SEPARATOR.join(item.state for _call, item in asked),
            question=_ask([item for _call, item in asked]),
            preview={
                "count": sum(item.count for _call, item in asked),
                "touched": touched[:PREVIEW_REFS],
            },
            expires_at=time.time() + TOKEN_TTL_SECONDS,
            more=[call for call, _item in asked[1:]],
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
    now = _STATE_SEPARATOR.join(str(_fingerprint(*call)) for call in pending.calls)
    if now != pending.state:
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


def _ask(items: list[_Asked]) -> str:
    """The question for one call, or for several asked about together."""
    if len(items) == 1:
        return f"Trash {items[0].phrase()}?"
    if all(item.kind == items[0].kind and not item.inside for item in items):
        names = [f'"{item.title}"' for item in items]
        return f"Trash {len(items)} {items[0].kind}s: {', '.join(names[:-1])} and {names[-1]}?"
    phrases = [item.phrase() for item in items]
    which = "both" if len(items) == 2 else f"all {len(items)}"
    return f"Trash {which} of these: {'; '.join(phrases[:-1])}; and {phrases[-1]}?"


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
