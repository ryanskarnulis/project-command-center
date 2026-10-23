"""Seeded random walks through the agent loop and the tool registry (#409).

The loop tests (``test_agent_loop.py``, ``test_agent_cancel.py``,
``test_agent_narrator.py`` …) each drive one feature: one Stop, one
deadline, one bad argument. The combinations nobody wrote a test for — a Stop
during the narrator call of a run that hit its tool-call cap, a DB error in the
second call of a batch after a schema correction — are what this drives.

A walk is a sequence of user messages posted through the real route
(``POST /api/agent/conversations/{id}/messages``) to the real
:class:`~app.ai.loop.AgentLoop` and the real tool registry, over an in-memory
database. A scripted provider (:class:`WalkProvider`) stands in for
llama-server: each message's script — tool batches chosen across the whole
registry off the live database, malformed calls, truncated turns, a final
reply — is drawn from a seeded :class:`random.Random`, and so is the one fault
a message may carry:

- ``provider_dies``: llama-server goes away on a given model call
  (``ProviderRequestError``);
- ``provider_crashes``: the provider raises something that is not a
  ``ProviderError`` (a bug in it);
- ``db_error``: SQLite fails a given statement inside a given tool call
  (``database is locked``);
- ``deadline``: the run's time budget runs out during a given model or tool
  call (a fake clock jumps past it), optionally with the provider then timing
  out;
- ``stop``: the user presses Stop during a given model or tool call.

After every message every invariant in :data:`INVARIANTS` is checked against
what the step observed: every tool call has exactly one answer, the saved
assistant turn is what ran, every mutation is audited under the run's actor,
nothing is hard-deleted, everything trashed is restorable, nothing starts after
Stop or the deadline, and a fault ends the run the way the loop documents.

The model is scripted and the database is fresh, so a walk is a pure function
of its seed: a breach is reported with the seed, the steps so far and the
command that replays it (:func:`replay_command`).
"""

from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Literal
from unittest import mock

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai import loop as loop_module
from app.ai import clarification, run_record
from app.ai.loop import AgentLoop, AgentRunFailed, AgentRunResult, resolve_actor
from app.ai.providers.llamacpp import (
    ChatResult,
    ProviderRequestError,
    ToolCall,
    ToolCallArgumentsError,
    ToolSpec,
)
from app.api import conversation_locks, rate_limit, routes_agent, run_cancel
from app.db.models import Base
from app.db.session import enable_sqlite_fk_enforcement, get_db, get_db_write
from app.main import app
from app.services import focus_plan as focus_plan_service
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import task_trash
from app.services import tasks as tasks_service
from app.services.tasks import OccurrenceConflictError
from app.tools import registry, runtime
from app.tools.registry import ToolError, UnknownToolError

# --- the step --------------------------------------------------------------------

Call = tuple[str, dict[str, Any]]

# Where a fault fires: during the n-th model call of the step, or the n-th
# tool call that reached the registry.
FaultSite = Literal["chat", "tool"]
FaultKind = Literal["provider_dies", "provider_crashes", "db_error", "deadline", "stop"]

# The narrator call's script (#418): a usable answer, a provider fault, or an
# empty reply (both of the latter fall back: to the planner's note on a
# completed run, to no reply on a limit stop).
NarrationMode = Literal["ok", "fails", "empty"]

NARRATION_TEXT = "Narration: here is what ran."
_MESSAGES = "/api/agent/conversations/{id}/messages"

# How many messages a corpus walk posts.
CORPUS_LENGTH = 12


@dataclass(frozen=True)
class Turn:
    """One scripted model turn.

    ``calls`` — a tool batch (one model response); ``bad_json`` — a response
    whose arguments are not a JSON object (``ToolCallArgumentsError``, naming
    ``text`` as the tool); ``text`` — a reply. ``truncated`` marks a turn the
    token cap cut off (``finish_reason == "length"``).
    """

    kind: Literal["calls", "bad_json", "text"]
    calls: tuple[Call, ...] = ()
    text: str = ""
    truncated: bool = False

    def describe(self) -> str:
        cut = " [cut off]" if self.truncated else ""
        if self.kind == "calls":
            return ", ".join(f"{name}({_args(args)})" for name, args in self.calls) + cut
        if self.kind == "bad_json":
            return f"<unparseable {self.text} call>"
        return f"text {self.text!r}{cut}"


@dataclass(frozen=True)
class Fault:
    """The one injected failure a step carries (see the module docstring).

    ``index`` is 0-based among the step's model calls (``site == "chat"``) or
    among the tool calls that reached the registry (``site == "tool"``);
    ``statement`` is which SQL statement of that tool call fails
    (``db_error``); ``then_raises`` makes a deadline crossed during a model
    call also end that call with a provider timeout.
    """

    kind: FaultKind
    site: FaultSite
    index: int
    statement: int = 0
    then_raises: bool = False

    def describe(self) -> str:
        extra = ""
        if self.kind == "db_error":
            extra = f", statement {self.statement}"
        if self.then_raises:
            extra = ", then the provider times out"
        return f"{self.kind} at {self.site} {self.index}{extra}"


@dataclass(frozen=True)
class Step:
    """One user message and everything scripted about its run."""

    turns: tuple[Turn, ...]
    fault: Fault | None = None
    narration: NarrationMode = "ok"
    actor_header: str | None = None
    conversation: int = 0
    max_iterations: int = 10
    max_corrections: int = 3
    max_tool_calls: int = loop_module.DEFAULT_MAX_TOOL_CALLS

    def describe(self) -> str:
        turns = " | ".join(turn.describe() for turn in self.turns)
        flags = [
            f"conv {self.conversation}",
            f"limits it={self.max_iterations} corr={self.max_corrections} "
            f"calls={self.max_tool_calls}",
        ]
        if self.actor_header is not None:
            flags.append(f"X-Agent-Actor={self.actor_header}")
        if self.narration != "ok":
            flags.append(f"narration {self.narration}")
        if self.fault is not None:
            flags.append(self.fault.describe())
        return f"{turns}  [{'; '.join(flags)}]"


def _args(args: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in args.items())


class InvariantBreach(AssertionError):
    """An invariant failed. Carries the whole reproduction in its message."""


# --- the database as the invariants read it ---------------------------------------

# Every table a run can write, directly or through a cascade. Snapshots are
# whole tables: a walk's database stays small.
_DOMAIN_TABLES = ("projects", "tasks", "task_dependencies", "focus_plan_entries", "focus_sessions")
_LOG_TABLES = ("activity_events", "conversations", "conversation_messages")
_TABLES = _DOMAIN_TABLES + _LOG_TABLES

Row = dict[str, Any]
Snapshot = dict[str, dict[int, Row]]


def snapshot(engine: Engine) -> Snapshot:
    """Every row of every table a run can touch, keyed by table then id."""
    with engine.connect() as conn:
        return {
            name: {
                int(row["id"]): dict(row)
                for row in conn.execute(select(Base.metadata.tables[name])).mappings()
            }
            for name in _TABLES
        }


def empty_snapshot() -> Snapshot:
    return {name: {} for name in _TABLES}


def _changed(
    before: Snapshot, after: Snapshot, table: str
) -> dict[int, tuple[Row | None, Row | None]]:
    """Rows of ``table`` that were added, changed or removed: id → (before, after)."""
    old, new = before[table], after[table]
    return {
        row_id: (old.get(row_id), new.get(row_id))
        for row_id in old.keys() | new.keys()
        if old.get(row_id) != new.get(row_id)
    }


@dataclass(frozen=True)
class World:
    """What the script draws ids from: the database at the start of a step."""

    tasks: tuple[int, ...] = ()
    trashed_tasks: tuple[int, ...] = ()
    recurring: tuple[int, ...] = ()
    projects: tuple[int, ...] = ()
    trashed_projects: tuple[int, ...] = ()
    entries: tuple[int, ...] = ()
    removed_entries: tuple[int, ...] = ()
    series: tuple[int, ...] = ()
    pins_today: tuple[int, ...] = ()
    dependencies: tuple[tuple[int, int], ...] = ()  # (edge id, dependent task id)

    def expect(self, call: Call) -> World:
        """The world as the script expects it after ``call`` succeeds.

        Only the trash moves: a step that trashes a row can then restore it.
        A guess — the call may be refused — which is all a script needs.
        """
        name, args = call
        moves: dict[str, tuple[str, str, str]] = {
            "trash_task": ("task_id", "tasks", "trashed_tasks"),
            "skip_occurrence": ("task_id", "tasks", "trashed_tasks"),
            "restore_task": ("task_id", "trashed_tasks", "tasks"),
            "trash_project": ("project_id", "projects", "trashed_projects"),
            "restore_project": ("project_id", "trashed_projects", "projects"),
            "remove_focus_entry": ("entry_id", "entries", "removed_entries"),
            "restore_focus_entry": ("entry_id", "removed_entries", "entries"),
        }
        if name not in moves or not isinstance(args.get(moves[name][0]), int):
            return self
        key, source, target = moves[name]
        row_id = int(args[key])
        if row_id not in getattr(self, source):
            return self
        return replace(
            self,
            **{
                source: tuple(i for i in getattr(self, source) if i != row_id),
                target: (*getattr(self, target), row_id),
            },
        )

    @classmethod
    def read(cls, snap: Snapshot) -> World:
        tasks = snap["tasks"].values()
        projects = snap["projects"].values()
        entries = snap["focus_plan_entries"].values()
        return cls(
            tasks=tuple(t["id"] for t in tasks if t["deleted_at"] is None),
            trashed_tasks=tuple(t["id"] for t in tasks if t["deleted_at"] is not None),
            recurring=tuple(
                t["id"] for t in tasks if t["deleted_at"] is None and t["recurrence_id"]
            ),
            projects=tuple(p["id"] for p in projects if p["deleted_at"] is None),
            trashed_projects=tuple(p["id"] for p in projects if p["deleted_at"] is not None),
            entries=tuple(e["id"] for e in entries if e["deleted_at"] is None),
            removed_entries=tuple(e["id"] for e in entries if e["deleted_at"] is not None),
            series=tuple(
                e["id"]
                for e in entries
                if e["deleted_at"] is None and e["repeat_interval"] and e["series_id"] is None
            ),
            pins_today=tuple(
                e["id"]
                for e in entries
                if e["deleted_at"] is None
                and e["task_id"] is not None
                and e["plan_date"] == date.today()
            ),
            dependencies=tuple(
                (d["id"], d["task_id"])
                for d in snap["task_dependencies"].values()
                if d["deleted_at"] is None
            ),
        )


# --- the generator -----------------------------------------------------------------

# An id no row has: a guess, as a model makes them.
_MISSING_ID = 9999
_TITLES = ("Buy paint", "Call the plumber", "Draft the plan", "Fix the gate", "Book flights")
_NAMES = ("Garage", "Taxes", "Trip", "Website")
_TIMES = ("08:00", "09:30", "13:00", "16:45")


def _pick(rng: random.Random, weighted: Sequence[tuple[str, int]]) -> str:
    names = [name for name, _ in weighted]
    weights = [weight for _, weight in weighted]
    return rng.choices(names, weights=weights, k=1)[0]


def _one(rng: random.Random, live: Sequence[int], dead: Sequence[int] = ()) -> int:
    """An id: mostly a live one, sometimes a trashed one, now and then a guess."""
    roll = rng.random()
    if live and roll < 0.75:
        return rng.choice(list(live))
    if dead and roll < 0.92:
        return rng.choice(list(dead))
    if live and roll < 0.95:
        return rng.choice(list(live))
    return _MISSING_ID


def _day(rng: random.Random) -> str:
    return (date.today() + timedelta(days=rng.randrange(-2, 10))).isoformat()


def _ahead(rng: random.Random) -> str:
    """A day a series that started today has: today or after."""
    return (date.today() + timedelta(days=rng.randrange(0, 6))).isoformat()


def _task_changes(rng: random.Random, world: World) -> dict[str, Any]:
    choice = rng.randrange(8)
    if choice == 0:
        return {"title": rng.choice(_TITLES)}
    if choice == 1:
        return {"priority": rng.choice(["low", "medium", "high", "urgent"])}
    if choice == 2:
        return {"due_date": _day(rng)}
    if choice == 3:
        return {"workflow_status": rng.choice(["open", "in_progress", "done"])}
    if choice == 4:
        return {"parent_task_id": _one(rng, world.tasks)}
    if choice == 5:
        return {"project_id": _one(rng, world.projects, world.trashed_projects)}
    if choice == 6:
        return {
            "repeat_interval": {"unit": rng.choice(["day", "week"]), "every": 1},
            "due_date": date.today().isoformat(),
        }
    return {"estimated_minutes": rng.choice([15, 30, 90])}


def _valid_call(rng: random.Random, world: World, name: str) -> Call:
    """A well-typed call to ``name`` — its ids may still be stale or guessed."""
    task = _one(rng, world.tasks, world.trashed_tasks)
    trashed_task = _one(rng, world.trashed_tasks, world.tasks)
    project = _one(rng, world.projects, world.trashed_projects)
    trashed_project = _one(rng, world.trashed_projects, world.projects)
    entry = _one(rng, world.entries, world.removed_entries)
    series = _one(rng, world.series, world.entries)
    calls: dict[str, Callable[[], dict[str, Any]]] = {
        "list_tasks": lambda: rng.choice(
            [{}, {"project_id": project}, {"exclude_done": True}, {"top_level_only": True}]
        ),
        "get_task": lambda: {"task_id": task},
        "create_task": lambda: {
            "title": rng.choice(_TITLES),
            **rng.choice(
                [
                    {},
                    {"project_id": project},
                    {"parent_task_id": _one(rng, world.tasks)},
                    {"due_date": _day(rng), "priority": "high"},
                    {"estimated_minutes": 30},
                ]
            ),
        },
        "update_task": lambda: {"task_id": task, **_task_changes(rng, world)},
        "complete_task": lambda: {
            "task_id": _one(rng, world.recurring or world.tasks, world.tasks)
        },
        "reopen_task": lambda: {"task_id": task},
        "trash_task": lambda: {"task_id": task},
        "restore_task": lambda: {
            "task_id": trashed_task,
            "restore_subtasks": rng.random() < 0.6,
        },
        "list_dependencies": lambda: {"task_id": task},
        "add_dependency": lambda: {
            "task_id": task,
            "depends_on_task_id": _one(rng, world.tasks, world.trashed_tasks),
        },
        "remove_dependency": lambda: (
            dict(zip(("dependency_id", "task_id"), rng.choice(world.dependencies), strict=True))
            if world.dependencies and rng.random() < 0.8
            else {"task_id": task, "dependency_id": _MISSING_ID}
        ),
        "skip_occurrence": lambda: {"task_id": _one(rng, world.recurring, world.tasks)},
        "stop_recurrence": lambda: {"task_id": _one(rng, world.recurring, world.tasks)},
        "list_projects": lambda: rng.choice([{}, {"include_closed": True}]),
        "get_project": lambda: {"project_id": project},
        "create_project": lambda: {"name": rng.choice(_NAMES)},
        "update_project": lambda: {
            "project_id": project,
            **rng.choice([{"name": rng.choice(_NAMES)}, {"description": "Notes"}]),
        },
        "close_project": lambda: {"project_id": project},
        "reopen_project": lambda: {"project_id": project},
        "trash_project": lambda: {"project_id": project},
        "restore_project": lambda: {
            "project_id": trashed_project,
            "restore_tasks": rng.random() < 0.6,
        },
        "search": lambda: {"query": rng.choice(["paint", "plan", "garage", "zzz"])},
        "get_focus_plan": lambda: rng.choice([{}, {"date": _day(rng)}]),
        "pin_focus_task": lambda: {
            "task_id": task,
            **rng.choice([{}, {"start_time": rng.choice(_TIMES)}, {"date": _day(rng)}]),
        },
        "add_focus_commitment": lambda: {
            "title": "Standup",
            "start_time": rng.choice(_TIMES),
            "duration_minutes": 30,
            **rng.choice([{}, {"repeat_interval": {"unit": "day", "every": 1}}]),
        },
        "skip_focus_occurrence": lambda: {"series_id": series, "plan_date": _ahead(rng)},
        "stop_focus_series": lambda: {
            "series_id": series,
            **rng.choice(
                [
                    {},
                    {"stop_date": (date.today() + timedelta(days=rng.randrange(1, 8))).isoformat()},
                ]
            ),
        },
        "edit_focus_occurrence": lambda: {
            "series_id": series,
            "plan_date": _ahead(rng),
            "start_time": rng.choice(_TIMES),
        },
        "reorder_focus_pins": lambda: {
            "entry_ids": rng.sample(list(world.pins_today), len(world.pins_today))
            if world.pins_today and rng.random() < 0.85
            else [_one(rng, world.entries)]
        },
        "remove_focus_entry": lambda: {"entry_id": entry},
        "restore_focus_entry": lambda: {
            "entry_id": _one(rng, world.removed_entries, world.entries)
        },
        "list_trash": lambda: {},
        "list_activity": lambda: {"project_id": project},
        "ask_user": lambda: {"task_ids": [task, _one(rng, world.tasks, world.trashed_tasks)]},
    }
    return name, calls[name]()


# Every registered tool, by how often a walk calls it. Writes outweigh reads:
# they are what the invariants have to hold over.
TOOL_WEIGHTS: dict[str, int] = {
    "list_tasks": 2,
    "get_task": 1,
    "create_task": 6,
    "update_task": 5,
    "complete_task": 4,
    "reopen_task": 2,
    "trash_task": 4,
    "restore_task": 4,
    "list_dependencies": 1,
    "add_dependency": 3,
    "remove_dependency": 2,
    "skip_occurrence": 3,
    "stop_recurrence": 1,
    "list_projects": 2,
    "get_project": 1,
    "create_project": 3,
    "update_project": 1,
    "close_project": 2,
    "reopen_project": 1,
    "trash_project": 3,
    "restore_project": 3,
    "search": 1,
    "get_focus_plan": 1,
    "pin_focus_task": 3,
    "add_focus_commitment": 2,
    "skip_focus_occurrence": 2,
    "stop_focus_series": 1,
    "edit_focus_occurrence": 1,
    "reorder_focus_pins": 1,
    "remove_focus_entry": 2,
    "restore_focus_entry": 2,
    "list_trash": 1,
    "list_activity": 1,
    "ask_user": 1,
}


def _malformed_call(rng: random.Random, world: World) -> Call:
    """A call the registry refuses on its schema — the shapes models emit."""
    task = _one(rng, world.tasks)
    shapes: list[Call] = [
        ("summon_intern", {}),
        ("create_task", {"titel": "Misspelled key"}),
        ("create_project", {"name": "Extra key", "priority": "high"}),
        ("create_task", {"title": "Bad priority", "priority": 3}),
        ("complete_task", {"task_id": "seven"}),
        ("update_task", {"task_id": task, "title": "   "}),
        ("create_task", {"title": "Bad id", "project_id": -3}),
        ("get_task", {}),
        ("pin_focus_task", {"task_id": task, "start_time": "25:99"}),
    ]
    return rng.choice(shapes)


def _call(rng: random.Random, world: World) -> Call:
    if rng.random() < 0.12:
        return _malformed_call(rng, world)
    return _valid_call(rng, world, _pick(rng, tuple(TOOL_WEIGHTS.items())))


# How often a step carries a fault, and which.
_FAULT_RATE = 0.45
_FAULTS: tuple[tuple[str, int], ...] = (
    ("provider_dies", 3),
    ("provider_crashes", 1),
    ("db_error", 3),
    ("deadline", 3),
    ("stop", 4),
)


def next_step(rng: random.Random, world: World, index: int) -> Step:
    """The walk's next message, drawn from the database it starts on."""
    turns: list[Turn] = []
    for _ in range(rng.choice([0, 1, 1, 2, 2, 3, 4])):
        if rng.random() < 0.08:
            turns.append(Turn("bad_json", text=rng.choice(["create_task", "update_task"])))
            continue
        batch: list[Call] = []
        for _ in range(rng.choice([1, 1, 1, 2, 2, 3, 5])):
            batch.append(_call(rng, world))
            # Later calls of the step see what this one would do, so a step can
            # trash something and restore it again.
            world = world.expect(batch[-1])
        turns.append(Turn("calls", calls=tuple(batch), truncated=rng.random() < 0.05))
    truncated = rng.random() < 0.06
    turns.append(
        Turn("text", text=f"Reply {index}." if not truncated else "Cut of", truncated=truncated)
    )
    calls = sum(len(turn.calls) for turn in turns)
    fault: Fault | None = None
    if rng.random() < _FAULT_RATE:
        kind = _pick(rng, _FAULTS)
        if kind == "db_error":
            fault = Fault("db_error", "tool", rng.randrange(max(calls, 1)), rng.randrange(6))
        elif kind in ("provider_dies", "provider_crashes"):
            fault = Fault(kind, "chat", rng.randrange(len(turns) + 1))  # type: ignore[arg-type]
        else:
            site: FaultSite = "tool" if calls and rng.random() < 0.5 else "chat"
            fault = Fault(
                kind,  # type: ignore[arg-type]
                site,
                rng.randrange(max(calls, 1)) if site == "tool" else rng.randrange(len(turns) + 1),
                then_raises=kind == "deadline" and site == "chat" and rng.random() < 0.3,
            )
    return Step(
        turns=tuple(turns),
        fault=fault,
        narration=_pick(rng, (("ok", 8), ("fails", 1), ("empty", 1))),  # type: ignore[arg-type]
        actor_header=rng.choice([None, None, None, "agent:conductor", "agent:nobody"]),
        conversation=rng.randrange(2),
        max_iterations=rng.choice([2, 3, 10, 10, 10]),
        max_corrections=rng.choice([0, 1, 3, 3]),
        max_tool_calls=rng.choice([3, 24, 24, 24]),
    )


# --- what a step observed ---------------------------------------------------------


@dataclass
class ChatEvent:
    """One model call the loop started."""

    index: int
    narrator: bool  # offered no tools: the narrator (#418)
    after_stop: bool  # Stop had already been pressed when it started
    after_deadline: bool  # the deadline had already passed when it started
    messages: list[dict[str, Any]]
    fault: bool = False  # the step's fault fired during this call


@dataclass
class ToolEvent:
    """One tool call that reached the registry."""

    index: int
    name: str
    arguments: dict[str, Any]
    actor: str
    after_stop: bool
    after_deadline: bool
    before: Snapshot
    after: Snapshot
    raised: BaseException | None = None
    fault: bool = False  # the step's fault fired during this call

    @property
    def domain_refusal(self) -> bool:
        """Refused in a way the model can read (schema or domain), not a crash."""
        return isinstance(self.raised, (ToolError, UnknownToolError, ValidationError))


@dataclass
class Observed:
    """What one step did, as the invariants read it."""

    step: Step
    actor: str = loop_module.LOOP_ACTOR
    status_code: int = 200
    response: dict[str, Any] = field(default_factory=dict)
    result: AgentRunResult | None = None
    chats: list[ChatEvent] = field(default_factory=list)
    tools: list[ToolEvent] = field(default_factory=list)
    before: Snapshot = field(default_factory=lambda: empty_snapshot())
    after: Snapshot = field(default_factory=lambda: empty_snapshot())
    # The conversation's messages this step added, oldest first.
    saved: list[Row] = field(default_factory=list)
    # Trashed rows a restore refused, with why.
    unrestorable: list[str] = field(default_factory=list)
    # The run's trace records (#400).
    traces: list[dict[str, Any]] = field(default_factory=list)
    stopped: bool = False  # the step's Stop fired
    deadline_passed: bool = False  # the step's deadline fired
    # Whether a known bug's shape passes (the corpus) or fails (a named case),
    # and which ones this step showed.
    tolerate_known_bugs: bool = True
    known_bugs: list[str] = field(default_factory=list)


# --- the app under the walk --------------------------------------------------------


class _Clock:
    """``time.monotonic`` for the loop and the route, which a fault can jump."""

    def __init__(self) -> None:
        self.offset = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.offset


class _Tracer:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


class _Run:
    """The bookkeeping of the step in flight."""

    def __init__(self, step: Step, number: int, conversation_id: int) -> None:
        self.step = step
        self.number = number
        self.conversation_id = conversation_id
        self.turns = list(step.turns)
        self.chats: list[ChatEvent] = []
        self.tools: list[ToolEvent] = []
        self.stopped = False
        self.deadline_passed = False
        self.result: AgentRunResult | None = None


class WalkProvider:
    """The scripted llama-server: plays the step's turns and fires its fault."""

    def __init__(self, harness: Harness) -> None:
        self._harness = harness

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
        timeout: float | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        run = self._harness.run
        assert run is not None, "the provider was called outside a step"
        event_ = ChatEvent(
            index=len(run.chats),
            narrator=tools is None,
            after_stop=run.stopped,
            after_deadline=run.deadline_passed,
            messages=[dict(message) for message in messages],
        )
        run.chats.append(event_)
        fault = run.step.fault
        if fault is not None and fault.site == "chat" and fault.index == event_.index:
            event_.fault = True
            if fault.kind == "provider_dies":
                raise ProviderRequestError("injected: llama-server went away")
            if fault.kind == "provider_crashes":
                raise RuntimeError("injected: a bug in the provider")
            if fault.kind == "stop":
                self._harness.press_stop()
            if fault.kind == "deadline":
                self._harness.pass_deadline()
                if fault.then_raises:
                    raise ProviderRequestError("injected: llama-server request timed out")
        if tools is None:
            if run.step.narration == "fails":
                raise ProviderRequestError("injected: the narrator call failed")
            return _text(NARRATION_TEXT if run.step.narration == "ok" else "")
        if not run.turns:
            return _text("Nothing left to say.")
        turn = run.turns.pop(0)
        if turn.kind == "bad_json":
            raise ToolCallArgumentsError(turn.text, "arguments are not valid JSON")
        if turn.kind == "text":
            return _text(turn.text, truncated=turn.truncated)
        return ChatResult(
            content="Working on it." if turn.truncated else None,
            tool_calls=[
                ToolCall(id=f"call_{run.number}_{event_.index}_{i}", name=name, arguments=args)
                for i, (name, args) in enumerate(turn.calls)
            ],
            finish_reason="length" if turn.truncated else "tool_calls",
            usage=None,
        )


def _text(content: str, *, truncated: bool = False) -> ChatResult:
    return ChatResult(
        content=content,
        tool_calls=[],
        finish_reason="length" if truncated else "stop",
        usage=None,
    )


class _CapturingLoop(AgentLoop):
    """The real loop; keeps the run's result (or a failure's partial one)."""

    def __init__(self, harness: Harness, step: Step) -> None:
        super().__init__(
            harness.provider,
            max_iterations=step.max_iterations,
            max_corrections=step.max_corrections,
            max_tool_calls=step.max_tool_calls,
            tracer=harness.tracer,
        )
        self._harness = harness

    def run(self, user_message: str, **kwargs: Any) -> AgentRunResult:
        run = self._harness.run
        assert run is not None
        try:
            result = super().run(user_message, **kwargs)
        except AgentRunFailed as exc:
            run.result = exc.result
            raise
        run.result = result
        return result


class Harness:
    """The shipped app over a scripted provider and a fresh in-memory database."""

    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        enable_sqlite_fk_enforcement(self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.factory: sessionmaker[Session] = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        self.session = self.factory()
        self.clock = _Clock()
        self.tracer = _Tracer()
        self.provider = WalkProvider(self)
        self.run: _Run | None = None
        self.steps = 0
        self._real_call_tool = registry.call_tool
        self._db_fault: tuple[int, list[int]] | None = None  # (statement, counter)
        self._stack = ExitStack()
        self._seed_world()
        try:
            self._stack.enter_context(mock.patch.object(runtime, "session_factory", self.factory))
            self._stack.enter_context(mock.patch.object(registry, "call_tool", self._call_tool))
            self._stack.enter_context(mock.patch.object(loop_module, "time", self.clock))
            self._stack.enter_context(mock.patch.object(routes_agent, "time", self.clock))
            event.listen(self.engine, "before_cursor_execute", self._maybe_fail_statement)
            self._stack.callback(
                event.remove, self.engine, "before_cursor_execute", self._maybe_fail_statement
            )
            app.dependency_overrides[get_db] = self._session
            app.dependency_overrides[get_db_write] = self._session
            app.dependency_overrides[routes_agent.get_agent_loop] = self._loop
            self._stack.callback(app.dependency_overrides.clear)
            self.client = self._stack.enter_context(TestClient(app))
            self.conversations = [
                self.client.post("/api/agent/conversations", json={}).json()["id"] for _ in range(2)
            ]
        except BaseException:
            self.close()
            raise

    def _seed_world(self) -> None:
        """General (the migration's seed, which production always has), then,
        as the user, two projects with a few tasks, a subtask, a dependency, a
        daily recurring task, a pin and a daily commitment for today."""
        with self.factory() as db:
            projects_service.ensure_default_project(db)
            home = projects_service.create_project(db, name="Home")
            work = projects_service.create_project(db, name="Work")
            paint = tasks_service.create_task(db, project_id=home.id, title="Buy paint")
            tasks_service.create_task(
                db, project_id=home.id, title="Pick a colour", parent_task_id=paint.id
            )
            plan = tasks_service.create_task(db, project_id=work.id, title="Draft the plan")
            ship = tasks_service.create_task(db, project_id=work.id, title="Ship it")
            deps_service.add_dependency(db, ship.id, plan.id)
            water = tasks_service.create_task(db, project_id=home.id, title="Water plants")
            tasks_service.update_task(
                db,
                water,
                {"repeat_interval": {"unit": "day", "every": 1}, "due_date": date.today()},
            )
            focus_plan_service.pin_task(db, plan_date=date.today(), task=paint)
            focus_plan_service.add_commitment(
                db,
                plan_date=date.today(),
                title="Standup",
                start_minute=9 * 60,
                duration_minutes=15,
                repeat_interval={"unit": "day", "every": 1},
            )
            db.commit()

    def _session(self) -> Iterator[Session]:
        # One session for both of a request's dependencies, as the ``client``
        # fixture does (on StaticPool two would BEGIN twice on one
        # connection), closed after the request so no transaction outlives it.
        try:
            yield self.session
        finally:
            self.session.close()

    def _loop(self) -> AgentLoop:
        assert self.run is not None
        return _CapturingLoop(self, self.run.step)

    def press_stop(self) -> None:
        assert self.run is not None
        self.run.stopped = True
        assert run_cancel.request_cancel(self.run.conversation_id)

    def pass_deadline(self) -> None:
        assert self.run is not None
        self.run.deadline_passed = True
        # Far past any budget the route could have set.
        self.clock.offset += 10 * 3600.0

    def _maybe_fail_statement(self, *_args: Any) -> None:
        if self._db_fault is None:
            return
        statement, counter = self._db_fault
        counter.append(1)
        if len(counter) - 1 == statement:
            assert self.run is not None
            self.run.tools[-1].fault = True
            raise sqlite3.OperationalError("database is locked")

    def _call_tool(self, name: str, arguments: dict[str, Any], *, actor: str) -> Any:
        run = self.run
        assert run is not None, "a tool was called outside a step"
        tool_event = ToolEvent(
            index=len(run.tools),
            name=name,
            arguments=arguments,
            actor=actor,
            after_stop=run.stopped,
            after_deadline=run.deadline_passed,
            before=snapshot(self.engine),
            after={},
        )
        run.tools.append(tool_event)
        fault = run.step.fault
        here = fault is not None and fault.site == "tool" and fault.index == tool_event.index
        if here and fault is not None and fault.kind == "db_error":
            self._db_fault = (fault.statement, [])
        try:
            return self._real_call_tool(name, arguments, actor=actor)
        except BaseException as exc:
            tool_event.raised = exc
            raise
        finally:
            self._db_fault = None
            tool_event.after = snapshot(self.engine)
            # A Stop or the deadline landing while the tool runs: the tool is
            # never interrupted, the next boundary must notice.
            if here and fault is not None and fault.kind == "stop":
                tool_event.fault = True
                self.press_stop()
            if here and fault is not None and fault.kind == "deadline":
                tool_event.fault = True
                self.pass_deadline()

    def world(self) -> World:
        return World.read(snapshot(self.engine))

    def take(self, step: Step) -> Observed:
        """Post the step's message and record everything it did."""
        self.steps += 1
        conversation_id = self.conversations[step.conversation]
        before = snapshot(self.engine)
        traced = len(self.tracer.records)
        self.run = _Run(step, self.steps, conversation_id)
        rate_limit._reset()
        headers = {} if step.actor_header is None else {"X-Agent-Actor": step.actor_header}
        try:
            response = self.client.post(
                _MESSAGES.format(id=conversation_id),
                json={"content": f"Message {self.steps}"},
                headers=headers,
            )
        finally:
            run, self.run = self.run, None
        after = snapshot(self.engine)
        saved = sorted(
            (
                row
                for row_id, row in after["conversation_messages"].items()
                if row_id not in before["conversation_messages"]
                and row["conversation_id"] == conversation_id
            ),
            key=lambda row: int(row["id"]),
        )
        return Observed(
            step=step,
            actor=resolve_actor(step.actor_header),
            status_code=response.status_code,
            response=response.json(),
            result=run.result,
            chats=run.chats,
            tools=run.tools,
            before=before,
            after=after,
            saved=saved,
            unrestorable=self._unrestorable(after),
            traces=self.tracer.records[traced:],
            stopped=run.stopped,
            deadline_passed=run.deadline_passed,
        )

    def _unrestorable(self, snap: Snapshot) -> list[str]:
        """Restore every trashed task and project, each in a transaction rolled back.

        A refusal the service documents — a recurring occurrence whose date a
        live sibling holds (``OccurrenceConflictError``) — is not a breach: the
        user trashes or skips that one first. Anything else is.
        """
        refused: list[str] = []
        targets: list[tuple[str, int]] = [
            ("task", row_id)
            for row_id, row in snap["tasks"].items()
            if row["deleted_at"] is not None
        ] + [
            ("project", row_id)
            for row_id, row in snap["projects"].items()
            if row["deleted_at"] is not None
        ]
        for kind, row_id in targets:
            db = self.factory()
            try:
                if kind == "task":
                    task = task_trash.get_deleted_task(db, row_id)
                    if task is None:
                        refused.append(f"task {row_id} is trashed but not in the trash")
                        continue
                    task_trash.restore_task_subtree(db, task)
                else:
                    project = projects_service.get_deleted_project(db, row_id)
                    if project is None:
                        refused.append(f"project {row_id} is trashed but not in the trash")
                        continue
                    projects_service.restore_project(db, project, restore_tasks=True)
                db.flush()
            except OccurrenceConflictError:
                pass
            except Exception as exc:
                refused.append(f"{kind} {row_id}: {type(exc).__name__}: {exc}")
            finally:
                db.rollback()
                db.close()
        return refused

    def close(self) -> None:
        self.session.close()
        self._stack.close()
        self.engine.dispose()
        rate_limit._reset()
        conversation_locks._reset()
        run_cancel._reset()


# --- known bugs ----------------------------------------------------------------------

# Real defects the walks found in app code, tolerated by the corpus (so it stays
# a gate for everything else) and reproduced, strictly, by the named cases in
# ``test_trajectories.py`` (xfail until fixed). Delete an entry with its fix:
# the named case then passes and its xfail turns strict-red, and the corpus
# stops tolerating the shape.
KNOWN_BUGS: dict[str, str] = {}


def _known(observed: Observed, bug: str) -> bool:
    """Whether to let a known bug's shape pass (and note that it was seen)."""
    if bug not in KNOWN_BUGS or not observed.tolerate_known_bugs:
        return False
    observed.known_bugs.append(bug)
    return True


# --- the invariants ------------------------------------------------------------------


def check_every_call_has_one_answer(observed: Observed) -> None:
    """Every tool call in the transcript — and in every request the model was
    sent — is answered by exactly one ``role: tool`` message, straight after
    its batch, in order; no answer is an orphan."""
    transcripts: list[tuple[str, list[dict[str, Any]]]] = [
        (f"model call {chat.index}", chat.messages) for chat in observed.chats
    ]
    if observed.result is not None:
        transcripts.append(("the run's transcript", observed.result.messages))
    for where, messages in transcripts:
        index = 0
        while index < len(messages):
            message = messages[index]
            index += 1
            if message.get("role") == "tool":
                raise InvariantBreach(f"{where}: an answer with no call: {message}")
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            ids = [call["id"] for call in message["tool_calls"]]
            answers = messages[index : index + len(ids)]
            answered = [m.get("tool_call_id") for m in answers if m.get("role") == "tool"]
            if answered != ids:
                raise InvariantBreach(f"{where}: calls {ids} answered by {answered}")
            index += len(ids)


def _ran(record_error: str | None) -> bool:
    """Whether a record's call reached the registry (a refusal still did)."""
    return record_error is None or not record_error.startswith(run_record.NOT_RUN_PREFIX)


def check_saved_turn_is_what_ran(observed: Observed) -> None:
    """The persisted assistant turn is the run's result, and the result is what
    the registry actually saw: the same calls, in order, each with the outcome
    it had."""
    result = observed.result
    if result is None:
        raise InvariantBreach(f"no run result (HTTP {observed.status_code}: {observed.response})")
    roles = [
        str(row["role"].value if hasattr(row["role"], "value") else row["role"])
        for row in observed.saved
    ]
    if roles != ["user", "assistant"]:
        raise InvariantBreach(f"the step saved {roles}, not one user and one assistant turn")
    saved = observed.saved[1]
    records = [record.model_dump() for record in result.tool_calls]
    if saved["content"] != result.reply or saved["stop_reason"] != result.stop_reason:
        raise InvariantBreach(
            f"saved ({saved['stop_reason']}, {saved['content']!r}) but the run ended "
            f"({result.stop_reason}, {result.reply!r})"
        )
    if (saved["tool_calls"] or []) != records:
        raise InvariantBreach(
            f"saved tool calls {saved['tool_calls']} but the run recorded {records}"
        )
    dispatched = [(tool.name, tool.arguments) for tool in observed.tools]
    recorded = [(r.tool, r.arguments) for r in result.tool_calls if _ran(r.error)]
    if dispatched != recorded:
        raise InvariantBreach(f"the registry ran {dispatched} but the turn says {recorded}")
    ran = [r for r in result.tool_calls if _ran(r.error)]
    for record, tool in zip(ran, observed.tools, strict=True):
        if (record.result is not None) != (tool.raised is None):
            raise InvariantBreach(
                f"{tool.name} {'raised ' + repr(tool.raised) if tool.raised else 'returned'}"
                f" but its record says result={record.result!r} error={record.error!r}"
            )
        if tool.raised is not None and not tool.domain_refusal:
            if record.error != loop_module._INTERNAL_ERROR:
                raise InvariantBreach(f"{tool.name} crashed but its record says {record.error!r}")
    expected = (
        200
        if result.stop_reason not in loop_module._FAILURE_STATUS
        else (loop_module._FAILURE_STATUS[result.stop_reason])
    )
    if observed.status_code != expected:
        raise InvariantBreach(
            f"stop reason {result.stop_reason} answered HTTP {observed.status_code}"
        )
    if observed.status_code == 200:
        answered = observed.response["assistant_message"]
        if answered["id"] != saved["id"] or answered["stop_reason"] != result.stop_reason:
            raise InvariantBreach(f"the response is not the saved turn: {answered}")
    stops = [record.get("stop_reason") for record in observed.traces]
    if stops != [result.stop_reason]:
        raise InvariantBreach(f"the run left traces {stops}, not one {result.stop_reason}")


def _explained(
    table: str, row_id: int, rows: tuple[Row | None, Row | None], events: list[Row]
) -> bool:
    """Whether a changed row is named by one of the call's activity events.

    A row is explained by an event about itself, or about the entity whose
    write cascaded to it: a task by its project (project trash/restore) or by
    any task event of the same call (a subtree trash/restore, a completion's
    roll-up and next occurrence, a re-parent); a dependency edge by an event on
    either of its tasks; a focus entry by an event about it, its series, the
    pin reorder (entity 0), or its task.
    """
    keys = {(str(e["entity_type"]), int(e["entity_id"])) for e in events}
    either = [row for row in rows if row is not None]
    if table == "projects":
        return ("project", row_id) in keys
    if table == "tasks":
        return any(kind == "task" for kind, _ in keys) or any(
            ("project", int(row["project_id"])) in keys for row in either
        )
    if table == "task_dependencies":
        return any(
            ("task", int(row["task_id"])) in keys
            or ("task", int(row["depends_on_task_id"])) in keys
            for row in either
        )
    if table == "focus_plan_entries":
        return (
            ("focus_entry", row_id) in keys
            or ("focus_entry", 0) in keys
            or any(
                ("focus_entry", int(row["series_id"] or 0)) in keys
                or ("task", int(row["task_id"] or 0)) in keys
                for row in either
            )
        )
    return ("focus_session", row_id) in keys


def _renumbered_only(tool: ToolEvent) -> bool:
    """A reorder that kept the pins' order and only renumbered them ``0..n-1``.

    ``focus_plan.reorder_pins`` logs a reorder only when the order moved; a
    renumber that leaves it as it was (tidying gaps or ties a removal or a
    restore left) changes nothing anyone can see, so it is not a mutation the
    audit owes an event for.
    """
    changed = _changed(tool.before, tool.after, "focus_plan_entries")
    if not changed or any(
        _changed(tool.before, tool.after, table)
        for table in _DOMAIN_TABLES
        if table != "focus_plan_entries"
    ):
        return False
    for old, new in changed.values():
        if old is None or new is None:
            return False
        if {key for key in old if old[key] != new[key]} - {"position", "updated_at"}:
            return False

    def order(snap: Snapshot) -> list[int]:
        pins = [
            row
            for row in snap["focus_plan_entries"].values()
            if row["deleted_at"] is None and row["task_id"] is not None
        ]
        return [
            int(row["id"])
            for row in sorted(pins, key=lambda r: (r["plan_date"], r["position"], r["id"]))
        ]

    return order(tool.before) == order(tool.after)


def check_every_mutation_is_audited(observed: Observed) -> None:
    """Per tool call: a refused or crashed call writes nothing; a read writes
    nothing; a write's every changed row is named by an activity event the call
    added, and every event it added carries the run's actor."""
    read_only = registry.read_only_tool_names()
    for tool in observed.tools:
        events = [
            row
            for row_id, row in tool.after["activity_events"].items()
            if row_id not in tool.before["activity_events"]
        ]
        changed = {table: _changed(tool.before, tool.after, table) for table in _DOMAIN_TABLES}
        touched = {table: sorted(rows) for table, rows in changed.items() if rows}
        label = f"tool call {tool.index} {tool.name}({_args(tool.arguments)})"
        if tool.actor != observed.actor:
            raise InvariantBreach(f"{label} ran as {tool.actor}, the run is {observed.actor}")
        if tool.raised is not None or tool.name in read_only:
            if touched or events:
                why = f"raised {tool.raised!r}" if tool.raised is not None else "is a read"
                raise InvariantBreach(f"{label} {why} but wrote {touched} and {len(events)} events")
            continue
        strangers = [e for e in events if e["actor"] != observed.actor]
        if strangers:
            raise InvariantBreach(
                f"{label} logged {[(e['action'], e['actor']) for e in strangers]} "
                f"as someone other than {observed.actor}"
            )
        if tool.name == "reorder_focus_pins" and not events and _renumbered_only(tool):
            continue
        for table, rows in changed.items():
            for row_id, pair in rows.items():
                if not _explained(table, row_id, pair, events):
                    raise InvariantBreach(
                        f"{label} changed {table} {row_id} with no activity event for it "
                        f"(events: {[(e['entity_type'], e['entity_id'], e['action']) for e in events]})"
                    )


def check_nothing_is_hard_deleted(observed: Observed) -> None:
    """Every row the step started with is still there (a trash is a
    ``deleted_at``, never a missing row), and the audit log and the
    conversation messages are append-only. The step before ended on this one's
    ``before``, so across a walk this covers every row ever written."""
    for table, rows in observed.before.items():
        missing = sorted(rows.keys() - observed.after[table].keys())
        if missing:
            raise InvariantBreach(f"{table} rows {missing} were hard-deleted")
    for table in ("activity_events", "conversation_messages"):
        for row_id, row in observed.before[table].items():
            if observed.after[table].get(row_id) != row:
                raise InvariantBreach(f"{table} row {row_id} was rewritten")


def check_trashed_is_restorable(observed: Observed) -> None:
    """Every trashed task and project can be restored (with what it took along)."""
    if observed.unrestorable:
        raise InvariantBreach(f"not restorable: {observed.unrestorable}")


def check_nothing_starts_after_stop_or_deadline(observed: Observed) -> None:
    """Once Stop is pressed or the deadline passes, no model call and no tool
    call starts — neither the next turn nor the narrator."""
    for chat in observed.chats:
        if chat.after_stop or chat.after_deadline:
            why = "Stop" if chat.after_stop else "the deadline"
            kind = "narrator" if chat.narrator else "model"
            raise InvariantBreach(f"{kind} call {chat.index} started after {why}")
    for tool in observed.tools:
        if tool.after_stop or tool.after_deadline:
            why = "Stop" if tool.after_stop else "the deadline"
            raise InvariantBreach(f"tool call {tool.index} {tool.name} started after {why}")


_LIMITS = frozenset(
    {"max_iterations", "correction_limit", "truncated", "no_progress", "tool_call_limit"}
)


def check_fault_ends_the_run_as_documented(observed: Observed) -> None:
    """A fault that fired ends the run the way ``app/ai/loop.py`` says it does.

    The one boundary-less case is a fault during the run's *last* model call:
    a reply or a narration already in flight completes, so the run keeps the
    stop it was heading for. A fault in the narrator call never changes the
    stop either (#418): its words are dropped, and a completed run replies
    with the planner's note. A limit stop after Stop or the deadline carries
    no narration (it would be a call started after them).
    """
    result = observed.result
    fault = observed.step.fault
    if result is None or fault is None:
        return
    fired = [c for c in observed.chats if c.fault] + [t for t in observed.tools if t.fault]
    if not fired:
        return
    reason = result.stop_reason
    last_chat = bool(observed.chats) and observed.chats[-1].fault
    narrator_chat = last_chat and observed.chats[-1].narrator
    if fault.kind in ("stop", "deadline"):
        expected = "cancelled" if fault.kind == "stop" else "timed_out"
        if fault.then_raises and not narrator_chat:
            expected = "timed_out"
        allowed = {expected}
        if last_chat and not fault.then_raises:
            allowed |= {"completed"} | _LIMITS
        if fault.site == "tool":
            allowed |= _LIMITS
        if narrator_chat:
            allowed |= {"completed"} | _LIMITS
        if reason not in allowed:
            raise InvariantBreach(f"{fault.describe()} fired but the run ended {reason}")
        if reason in _LIMITS and result.reply is not None and not narrator_chat:
            raise InvariantBreach(f"a {reason} stop after {fault.kind} was still narrated")
        return
    if narrator_chat:
        # A failed narration is dropped, never the run (#399, #418). A run
        # that ended on a landed ask replies with code's question (#420).
        if reason == "completed":
            asked = [
                record
                for record in result.tool_calls
                if record.tool == clarification.ASK_TOOL and record.result is not None
            ]
            fallback = (
                clarification.ask_text(clarification.candidates_of(asked[0].result or ""))
                if asked
                else result.note
            )
            if result.reply != fallback:
                raise InvariantBreach(
                    f"{fault.describe()} in the narrator call left reply {result.reply!r}, "
                    f"not {fallback!r}"
                )
        elif reason not in _LIMITS or result.reply is not None:
            raise InvariantBreach(
                f"{fault.describe()} in the narrator call ended the run {reason} "
                f"with reply {result.reply!r}"
            )
        return
    expected = {
        "provider_dies": "provider_error",
        "provider_crashes": "internal_error",
        "db_error": "internal_error",
    }[fault.kind]
    if reason != expected:
        raise InvariantBreach(f"{fault.describe()} fired but the run ended {reason}")


INVARIANTS: tuple[Callable[[Observed], None], ...] = (
    check_every_call_has_one_answer,
    check_saved_turn_is_what_ran,
    check_every_mutation_is_audited,
    check_nothing_is_hard_deleted,
    check_trashed_is_restorable,
    check_nothing_starts_after_stop_or_deadline,
    check_fault_ends_the_run_as_documented,
)


# --- a whole walk ------------------------------------------------------------------


def replay_command(seed: int, length: int) -> str:
    return (
        f"PCC_TRAJ_SEED={seed} PCC_TRAJ_LENGTH={length} "
        ".venv/bin/pytest tests/test_trajectories.py -k replay -s"
    )


def _check(log: list[Observed], seed: int | None, length: int) -> None:
    observed = log[-1]
    for invariant in INVARIANTS:
        try:
            invariant(observed)
        except InvariantBreach as breach:
            raise InvariantBreach(_report(seed, length, log, invariant, breach)) from breach


@contextmanager
def harness() -> Iterator[Harness]:
    built = Harness()
    try:
        yield built
    finally:
        built.close()


def walk(seed: int, length: int) -> list[Observed]:
    """Run one seeded walk, checking every invariant after every step.

    A breach is re-raised with the seed, every step up to and including the
    failing one, and the command that replays it.
    """
    rng = random.Random(seed)
    log: list[Observed] = []
    with harness() as app_under_walk:
        for index in range(length):
            step = next_step(rng, app_under_walk.world(), index)
            log.append(app_under_walk.take(step))
            _check(log, seed, length)
    return log


def run_steps(steps: Sequence[Step], *, tolerate_known_bugs: bool = False) -> list[Observed]:
    """Run a fixed sequence (a named case) under the same invariants.

    Strict by default: a known bug's shape is a breach here.
    """
    log: list[Observed] = []
    with harness() as app_under_walk:
        for step in steps:
            observed = app_under_walk.take(step)
            observed.tolerate_known_bugs = tolerate_known_bugs
            log.append(observed)
            _check(log, None, len(steps))
    return log


def _report(
    seed: int | None,
    length: int,
    log: Sequence[Observed],
    invariant: Callable[[Observed], None],
    breach: InvariantBreach,
) -> str:
    lines = [f"{invariant.__name__}: {breach}"]
    if seed is not None:
        lines.append(f"seed={seed} length={length}")
        lines.append(f"replay: {replay_command(seed, length)}")
    for index, observed in enumerate(log):
        reason = observed.result.stop_reason if observed.result is not None else "-"
        lines.append(f"  {index:>2}. {observed.step.describe()}")
        lines.append(f"      -> HTTP {observed.status_code} {reason}")
    return "\n".join(lines)


def describe(log: Sequence[Observed]) -> Iterator[str]:
    """One line per step and its outcome, for the replay test's output."""
    for index, observed in enumerate(log):
        result = observed.result
        reason = result.stop_reason if result is not None else "-"
        calls = (
            ", ".join(
                f"{r.tool}:{'ok' if r.result is not None else (r.error or '')[:40]}"
                for r in result.tool_calls
            )
            if result is not None
            else ""
        )
        yield f"{index:>2}. {observed.step.describe()}\n    -> {observed.status_code} {reason}: {calls}"


__all__ = [
    "CORPUS_LENGTH",
    "INVARIANTS",
    "TOOL_WEIGHTS",
    "ChatEvent",
    "Fault",
    "InvariantBreach",
    "Observed",
    "Step",
    "ToolEvent",
    "Turn",
    "describe",
    "replay_command",
    "run_steps",
    "walk",
]
