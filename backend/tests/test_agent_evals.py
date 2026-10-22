"""Agent eval harness: scripted scenarios against the REAL model (loop epic, slice 4).

Opt-in like the provider's live smoke — skipped unless ``PCC_AGENT_EVALS=1``,
so CI and default local runs never touch the GPU:

    cd backend
    PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s

Each sample seeds a fresh in-memory DB through the service layer, runs one
user ask through the full ``AgentLoop`` + shared tool registry against the
live runtime (``LLAMACPP_BASE_URL``, default :8200 gemma-4-12b) at the
model's profile (``app/ai/profiles/<LLAMACPP_MODEL>.toml``, #402), then asserts
two things:

* **trajectory shape** — behavioral, not exact sequences (the model is
  nondeterministic): the right tool family was used, reads preceded writes,
  nothing mutated on read-only asks;
* **DB end-state** — the mutation really happened (or really didn't),
  soft-delete/audit invariants included.

**The gate is statistical (#403).** Every decision about a count lives in
``tests/evalstats.py`` (pure, unit-tested in CI by ``test_evalstats.py``):

* **Block-sequential sampling.** Each scenario is one pytest case that takes
  a block of ``PCC_AGENT_EVALS_RUNS`` samples (default 5), then
  ``evalstats.decide``: a one-sided Wilson upper bound below the floor (0.8)
  is red, a point estimate at the floor is green, and only the ambiguous middle
  buys another block, up to ``PCC_AGENT_EVALS_MAX_RUNS`` (default 20). At the
  defaults 0–2/5 is red, 3/5 escalates, 4–5/5 is green. A green means *not
  statistically below the floor*, not "works 80% of the time". Setting
  ``PCC_AGENT_EVALS_RUNS`` at or above the cap forces a fixed count.
* **Infrastructure is retried, never scored.** A provider death (transport
  error, 5xx, timeout) or a 429 from the agent route is thrown away and
  re-taken, at most ``PCC_AGENT_EVALS_INFRA_RETRIES`` (5) per scenario and
  ``PCC_AGENT_EVALS_INFRA_BUDGET`` (25) across the suite; running out fails
  the scenario as INFRA_ABORTED with no rate. A request the server *rejected*
  (a 4xx, an unparseable body) fails on the first one: a retry gets the same
  answer. Route-driven samples get a fresh client (its own rate-limit key).
* **Paired seeds.** ``PCC_AGENT_EVALS_SEED=<base>`` sends llama-server the
  sampling seed ``base + j`` on every call of a scenario's ``j``-th scored
  sample (an infra retry re-takes the same seed). Unset — the default — sends
  no seed. Two arms run with one base pair sample for sample;
  ``python -m tests.eval_report A.jsonl B.jsonl`` prints the exact McNemar
  test on the pairs they disagree on.
* **A JSONL report.** ``PCC_AGENT_EVALS_REPORT=<path>`` appends a header
  (git sha, model, profile, prompt and tool-schema hashes, knobs, floors),
  one line per scenario as it finishes (verdict plus every sample), and a
  closing suite line with the infra budget spent. The header also prints.
* **Schema corrections (#404).** Every sample counts the calls refused for
  their arguments or an unknown tool name (``eval_report.schema_errors``);
  each scenario reports them per sample and per call beside its verdict.
* **Multi-turn threads (#389, #405).** ``Sample.converse`` drives a scripted
  conversation through the conversation route (stored history, as in
  production), metering and snapshotting each step and checking it at once.
  One sample is the whole thread: it passes only if every step does.
* **Claims checked against the run (#407).** Every reply is read for
  operational claims ("created", "marked done", "moved to Friday", a count, a
  quoted title, a date) and each is checked against what the sample's runs
  actually did (``tests/claims.py``, pinned by ``test_claims.py`` in CI). An
  unbacked claim fails the sample in every scenario; the claim-accuracy rate
  is reported per scenario and for the suite, never asserted, with every
  unbacked line quoted.
* **Compositions, dates and speech (#406).** Single-turn asks that are not one
  clean tool call: several writes in one ask (full end state, nothing else
  changed), relative dates computed from today's real date by pure helpers
  (``tests/eval_threads.py``), and speech-to-text misspellings the model has
  to read through itself (the app carries no normaliser, by design).

This suite is the tripwire from ``docs/agent-design.md``: baseline results
are recorded there. ``-s`` shows the per-sample ``[eval] …`` lines and the
end-of-suite table. The first call may cold-load the model (~100 s).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai import gather
from app.ai import trace as run_trace
from app.ai.loop import (
    DELEGATE_ACTORS,
    LOOP_ACTOR,
    SUMMARY_PROMPT_SHA256,
    SYSTEM_PROMPT_SHA256,
    AgentLoop,
    AgentRunFailed,
    AgentRunResult,
    ToolCallRecord,
)
from app.ai.providers.llamacpp import LlamaCppProvider, provider_from_settings
from app.ai.run_record import INVALID_ARGUMENTS_PREFIX
from app.api import routes_agent
from app.db.models import (
    ActivityEvent,
    Base,
    FocusPlanEntry,
    Project,
    Task,
    TaskDependency,
    TaskPriority,
    TaskWorkflowStatus,
)
from app.db.session import enable_sqlite_fk_enforcement, get_db, get_db_write
from app.main import app
from app.services import conversations as conversations_service
from app.services import focus_plan as focus_plan_service
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import task_trash
from app.services import tasks as tasks_service
from app.tools import registry, runtime
from tests.claims import (
    Claim,
    Evidence,
    Tally,
    ToolCall,
    concedes_miss,
    read_claims,
    unbacked,
)
from tests.eval_report import (
    RunRecord,
    format_report,
    int_from_env,
    schema_errors,
    schema_stats,
    summarize,
    unparseable_turns,
    write_jsonl,
)
from tests.eval_threads import (
    changed_keys,
    end_of_month,
    mention_order,
    next_friday_targets,
    next_month_bounds,
    weekday_targets,
    weekend_targets,
)
from tests.evalstats import (
    DEFAULT_FLOOR,
    Decision,
    Outcome,
    RateResult,
    classify,
    provider_failure_kind,
    sample_seed,
    scenario_record,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("PCC_AGENT_EVALS") != "1",
    reason="agent evals run the real model: set PCC_AGENT_EVALS=1",
)

# --- sampling knobs -------------------------------------------------------------
#
# All env-overridable, so a measurement campaign needs no code edit, and all
# written into the report header. The retry budgets follow chess's sizing
# (five per scenario is several times the expected deaths of a 20-sample
# escalation; 25 bounds a suite against a server that stopped coming back).
_BLOCK_RUNS = int_from_env("PCC_AGENT_EVALS_RUNS", 5) or 5
_MAX_RUNS = max(int_from_env("PCC_AGENT_EVALS_MAX_RUNS", 20) or 20, _BLOCK_RUNS)
_INFRA_RETRIES = int_from_env("PCC_AGENT_EVALS_INFRA_RETRIES", 5) or 5
_INFRA_BUDGET = int_from_env("PCC_AGENT_EVALS_INFRA_BUDGET", 25) or 25
_SEED_BASE = int_from_env("PCC_AGENT_EVALS_SEED", None, minimum=0)
_REPORT_PATH = os.environ.get("PCC_AGENT_EVALS_REPORT") or None

# Every scenario's floor. They are regression tripwires, not aspirations, and
# lowering one for quiet costs the gate its power (``evalstats.DEFAULT_FLOOR``).
_FLOORS: dict[str, float] = {
    name: DEFAULT_FLOOR
    for name in (
        "create_task_with_fields",
        "find_and_complete",
        "reschedule",
        "delete_is_soft",
        "read_only_count",
        "honest_about_missing",
        "delegate_actor",
        "focus_pin_and_commitment",
        "focus_remove_is_soft",
        "focus_skip_series",
        "dependency_add",
        "dependency_remove",
        "recurrence_make_repeating",
        "recurrence_skip",
        "recurrence_stop",
        "restore_task",
        "restore_project",
        "project_create",
        "project_rename",
        "project_close",
        "project_reopen",
        "project_trash",
        "misfire_batch_create",
        "misfire_create_in_named_project",
        "misfire_subtask_under_parent",
        "misfire_complete_parent",
        "misfire_edit_among_similar",
        *(
            f"{family}[{condition}]"
            for family in ("followup_high_priority", "long_complete", "long_reschedule")
            for condition in ("fresh", "live_like")
        ),
        "reference_rename_then_undo",
        "reference_trash_then_undo",
        "reference_first_to_friday",
        "delegate_thread",
        "compose_create_and_pin",
        "compose_move_due_today",
        "compose_finish_and_follow_up",
        "compose_wait_and_bump",
        "date_next_friday",
        "date_in_two_weeks",
        "date_end_of_month",
        "date_this_weekend",
        "speech_add_task",
        "speech_mark_done",
        "dependency_paraphrase",
    )
}

# What the agent route's failure statuses mean (``loop._FAILURE_STATUS``), plus
# the limiter's 429.
_ROUTE_STOPS = {
    429: "rate_limited",
    500: "internal_error",
    502: "provider_error",
    504: "timed_out",
}


@dataclass
class _SuiteTotals:
    """Totals across the run, and the infra ceiling shared by every scenario:
    without one, a dead llama-server turns the suite into an unbounded retry
    loop that never reports anything."""

    scenarios: int = 0
    samples: int = 0
    infra_spent: int = 0
    infra_budget: int = _INFRA_BUDGET

    def spend_infra(self) -> bool:
        """Charge one thrown-away sample; False once the suite is out of budget."""
        self.infra_spent += 1
        return self.infra_spent < self.infra_budget


_SUITE = _SuiteTotals()
_RECORDS: list[RunRecord] = []
_RESULTS: dict[str, RateResult] = {}
# Claims made and backed over every scored sample of the suite (#407).
_CLAIMS = Tally()


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    return completed.stdout.strip()


def _header(provider: LlamaCppProvider) -> dict[str, Any]:
    """What produced the numbers: a baseline without this is a rumour. The
    prompt and tool-schema hashes are the run trace's (#400), so a report and
    a trace from one build agree on them."""
    status = _git("status", "--porcelain")
    return {
        "kind": "header",
        "started": datetime.now(UTC).isoformat(),
        "git_sha": _git("rev-parse", "HEAD") or "unknown",
        "git_dirty": None if status is None else bool(status),
        "model": provider.model,
        "profile": provider.profile.describe(),
        "system_prompt_sha256": SYSTEM_PROMPT_SHA256,
        "summary_prompt_sha256": SUMMARY_PROMPT_SHA256,
        "tool_schemas_sha256": run_trace.tool_schemas_sha256(registry.tool_specs()),
        "knobs": {
            "block_runs": _BLOCK_RUNS,
            "max_runs": _MAX_RUNS,
            "infra_retries": _INFRA_RETRIES,
            "infra_budget": _INFRA_BUDGET,
            "seed": _SEED_BASE,
        },
        "floors": _FLOORS,
    }


@pytest.fixture(scope="module", autouse=True)
def _eval_session() -> Generator[None, None, None]:
    """Bracket the run: the header before the first scenario (so an
    interrupted run still says what produced its lines), the table and the
    infra budget spent after the last."""
    # The model's sampling, thinking kwarg and token caps come from its
    # profile (#402) — the same one the provider builds every request from —
    # so measuring another model is LLAMACPP_MODEL=<id> plus its profile file.
    with provider_from_settings() as provider:
        header = _header(provider)
        print(f"\n[eval] model={provider.model} profile={provider.profile.describe()}")
    print(f"[eval] header {json.dumps(header)}")
    write_jsonl(_REPORT_PATH, header)
    started = time.monotonic()
    yield
    suite = {
        "kind": "suite",
        "finished": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - started, 1),
        "scenarios": _SUITE.scenarios,
        "samples": _SUITE.samples,
        "infra_spent": _SUITE.infra_spent,
        "infra_budget": _SUITE.infra_budget,
        "claims": _CLAIMS.as_dict(),
    }
    write_jsonl(_REPORT_PATH, suite)
    print(f"\n{format_report(summarize(_RECORDS), _RESULTS)}")
    print("\n".join(f"[eval] {line}" for line in _CLAIMS.lines()))
    print(f"[eval] suite {json.dumps(suite)}")


def _is_read(tool: str) -> bool:
    return tool in registry.read_only_tool_names()


def _mutations(result: AgentRunResult) -> list[ToolCallRecord]:
    return [record for record in result.tool_calls if not _is_read(record.tool)]


def _successful(result: AgentRunResult, tool: str) -> list[ToolCallRecord]:
    return [
        record
        for record in result.tool_calls
        if record.tool == tool and record.error is None
    ]


_KNOWN_TOOLS = frozenset(tool.name for tool in registry.all_tools())


def _schema_errors(result: AgentRunResult | None, prompt: str | None) -> int:
    """The run's schema corrections (``eval_report.schema_errors``, #404)."""
    if result is None:
        return 0
    return schema_errors(
        ((record.tool, record.error) for record in result.tool_calls),
        known_tools=_KNOWN_TOOLS,
        invalid_prefix=INVALID_ARGUMENTS_PREFIX,
        unparseable=0 if prompt is None else unparseable_turns(result.messages, prompt),
    )


def _refusals(result: AgentRunResult | None) -> list[str]:
    """Every refused call's error text, schema or domain: the ``[eval]`` line
    shows only ``tool!``, and a red scenario is diagnosed from these."""
    if result is None:
        return []
    return [f"{r.tool}: {r.error}" for r in result.tool_calls if r.error is not None]


def _trajectory(result: AgentRunResult | None) -> str:
    if result is None:
        return ""
    return " → ".join(
        record.tool + ("!" if record.error is not None else "") for record in result.tool_calls
    )


class _RunDied(Exception):
    """The run ended without a verdict from the model; the :class:`Sample`
    holds why. Raised out of the scenario body so its checks never run on a
    turn that did not happen."""


def _warm_vectors() -> None:
    """Embed the sample's world before a run (#415), as a deployed instance has
    long since done by the time anyone asks: the gather's background warm would
    otherwise leave the first run of every sample on keywords alone. Vectors
    are keyed on each task's text, so a seed shared across samples embeds
    once. A no-op when no embeddings service is configured.

    Background warming is off: a sample's database is one shared in-memory
    connection, and a warm thread reading it mid-run collides with the run's
    own transaction ("cannot start a transaction within a transaction")."""
    searcher = gather.default_searcher()
    searcher.background_warm = False
    searcher.warm()


class _RecordingLoop(AgentLoop):
    """The real loop, keeping each run's actor + result for the asserts/stats.

    The route never returns ``AgentRunResult`` (iterations, stop reason), so the
    delegate scenario captures it here instead of reconstructing it.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.runs: list[tuple[str, AgentRunResult]] = []

    def run(
        self,
        user_message: str,
        *,
        history: Sequence[dict[str, Any]] | None = None,
        actor: str = LOOP_ACTOR,
        deadline: float | None = None,
        cancel: threading.Event | None = None,
    ) -> AgentRunResult:
        _warm_vectors()
        result = super().run(
            user_message,
            history=history,
            actor=actor,
            deadline=deadline,
            cancel=cancel,
        )
        self.runs.append((actor, result))
        return result


# --- Multi-turn threads (#389, #405) ---------------------------------------------
#
# A thread is driven through the conversation route, the only path where
# history exists: ``post_message`` reads the stored turns
# (``history_for_loop``), runs the loop on them, and stores the new pair. Each
# step is metered on its own, and the DB is snapshotted after it, because a
# later step changes the live rows a check about an earlier one would read.


@dataclass(frozen=True)
class TaskRow:
    """One task as a snapshot keeps it: the fields an ask might touch."""

    title: str
    project_id: int
    parent_task_id: int | None
    priority: str
    status: str
    due_date: date | None
    deleted: bool


# (entity_type, entity_id, action, actor) of one activity row.
EventRow = tuple[str, int, str, str | None]


@dataclass(frozen=True)
class World:
    """The DB at one moment: every task and project (trashed ones included)
    and the whole activity log, oldest first."""

    tasks: dict[int, TaskRow]
    projects: dict[int, tuple[str, bool, bool]]  # name, trashed, closed
    events: tuple[EventRow, ...]

    @classmethod
    def read(cls, db: Session) -> World:
        tasks = {
            t.id: TaskRow(
                title=t.title,
                project_id=t.project_id,
                parent_task_id=t.parent_task_id,
                priority=t.priority.value,
                status=t.workflow_status.value,
                due_date=t.due_date,
                deleted=t.deleted_at is not None,
            )
            for t in db.execute(select(Task)).scalars().all()
        }
        projects = {
            p.id: (p.name, p.deleted_at is not None, p.closed_at is not None)
            for p in db.execute(select(Project)).scalars().all()
        }
        events = tuple(
            (e.entity_type, e.entity_id, e.action, e.actor)
            for e in db.execute(select(ActivityEvent).order_by(ActivityEvent.id)).scalars()
        )
        return cls(tasks, projects, events)

    def task_id(self, title: str) -> int:
        """The id of the one task titled ``title`` (a seed's, so exact)."""
        ids = [task_id for task_id, row in self.tasks.items() if row.title == title]
        assert len(ids) == 1, f"seed has {len(ids)} tasks titled {title!r}"
        return ids[0]


@dataclass
class Step:
    """One turn of a thread: what was said, the run it got, and the DB after."""

    index: int
    ask: str
    actor: str
    result: AgentRunResult
    seconds: float
    world: World
    # The activity rows this step wrote.
    events: tuple[EventRow, ...]

    @property
    def reply(self) -> str:
        return self.result.reply or ""

    @property
    def schema_errors(self) -> int:
        return _schema_errors(self.result, self.ask)

    def record(self) -> dict[str, Any]:
        """The step as the report keeps it; the reply too, since the next step
        is read against it ("the second one")."""
        return {
            "step": self.index,
            "ask": self.ask,
            "actor": self.actor,
            "stop_reason": self.result.stop_reason,
            "iterations": self.result.iterations,
            "seconds": round(self.seconds, 1),
            "trajectory": _trajectory(self.result),
            "tool_calls": len(self.result.tool_calls),
            "schema_errors": self.schema_errors,
            "refusals": _refusals(self.result),
            "run_id": self.result.run_id,
            "reply": self.reply[:1000],
        }


@dataclass
class Thread:
    """A conversation as the steps so far left it. ``baseline`` is the DB once
    the seed (world and stored thread) was in, before the first step."""

    conversation_id: int
    baseline: World
    seeded_turns: int
    steps: list[Step] = field(default_factory=list)

    @property
    def step(self) -> Step:
        """The step being checked: the latest."""
        return self.steps[-1]

    @property
    def before(self) -> World:
        """The DB as the latest step found it."""
        return self.steps[-2].world if len(self.steps) > 1 else self.baseline


# A turn of a scripted thread: what the user says, and the check its step must
# pass, run right after it (a failed check ends the sample: one sample is the
# whole thread, and it passes only if every step does).
Turn = tuple[str, Callable[[Thread], None]]


def _stored_turn(reply: str) -> AgentRunResult:
    """An assistant turn for a seeded thread: text only, which is all
    ``history_for_loop`` ever sends back to the model."""
    return AgentRunResult(
        reply=reply, stop_reason="completed", tool_calls=[], iterations=1, messages=[]
    )


class Sample:
    """One sample's world: a fresh in-memory DB the loop's tool sessions are
    pointed at, the sample's seed, and what its run turned out to be."""

    def __init__(self, scenario: str, index: int, seed: int | None) -> None:
        self.scenario = scenario
        self.index = index
        self.seed = seed
        # StaticPool: one in-memory connection, visible from the TestClient's
        # worker thread (the same topology as conftest's ``test_engine``).
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        enable_sqlite_fk_enforcement(self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db: sessionmaker[Session] = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        self.status_code = 200
        self.stop_reason: str | None = None
        self.provider_failure: str | None = None
        self.detail: str | None = None
        self.result: AgentRunResult | None = None
        self.duration: float | None = None
        # The ask, so the schema count can find the run's turn in the transcript.
        self.prompt: str | None = None
        # A thread's completed steps (``converse``), and the one in flight.
        self.steps: list[Step] = []
        self.step_label: str | None = None
        # Every tool call this sample's runs made, and what each reply claimed
        # (with its step) read against the calls made so far (#407).
        self.calls: list[ToolCall] = []
        # And every task row a run was shown by the gather (#415).
        self.shown: list[dict[str, Any]] = []
        self.claims: list[tuple[str | None, Claim]] = []

    # A thread's sample is metered as the sum of its steps; each step's own
    # numbers are in the report's ``steps``.

    @property
    def tool_calls(self) -> int:
        if self.steps:
            return sum(len(step.result.tool_calls) for step in self.steps)
        return 0 if self.result is None else len(self.result.tool_calls)

    @property
    def schema_errors(self) -> int:
        if self.steps:
            return sum(step.schema_errors for step in self.steps)
        return _schema_errors(self.result, self.prompt)

    @property
    def iterations(self) -> int | None:
        if self.steps:
            return sum(step.result.iterations for step in self.steps)
        return None if self.result is None else self.result.iterations

    @property
    def trajectory(self) -> str:
        if self.steps:
            return " | ".join(_trajectory(step.result) or "-" for step in self.steps)
        return _trajectory(self.result)

    @property
    def missed_claims(self) -> list[tuple[str | None, Claim]]:
        """The scored claims no run of this sample backs (``claims.unbacked``)."""
        missed = unbacked(claim for _, claim in self.claims)
        return [(step, claim) for step, claim in self.claims if claim in missed]

    def provider(self) -> LlamaCppProvider:
        """The configured provider, sending this sample's seed (if any)."""
        return provider_from_settings(seed=self.seed)

    def run(self, prompt: str, *, max_iterations: int = 10) -> AgentRunResult:
        """One run straight through the loop."""
        started = time.monotonic()
        self.prompt = prompt
        _warm_vectors()
        with self.provider() as provider:
            try:
                result = AgentLoop(provider, max_iterations=max_iterations).run(prompt)
            except AgentRunFailed as exc:
                self._died(exc.http_status, exc.result.stop_reason, str(exc), exc.result, started)
        self._ran(result, started)
        return result

    @contextmanager
    def client(self) -> Iterator[tuple[TestClient, Session]]:
        """A TestClient over this sample's DB, with a client address of its own:
        the agent route allows 10 messages a minute *per client*, so samples
        sharing one would trip it mid-escalation."""
        session = self.db()

        def override() -> Generator[Session, None, None]:
            yield session

        app.dependency_overrides[get_db] = override
        app.dependency_overrides[get_db_write] = override
        try:
            with TestClient(app, client=(f"eval-{self.scenario}-{self.index}", 50000)) as client:
                yield client, session
        finally:
            app.dependency_overrides.clear()
            session.close()

    def post_message(
        self,
        client: TestClient,
        url: str,
        *,
        content: str,
        headers: dict[str, str],
        loop: _RecordingLoop,
    ) -> httpx.Response:
        """One run through the agent route; a failure status ends the sample."""
        started = time.monotonic()
        self.prompt = content
        response = client.post(url, json={"content": content}, headers=headers)
        if response.status_code != 200:
            try:
                detail = str(response.json().get("detail"))
            except ValueError:
                detail = response.text[:500]
            self._died(
                response.status_code,
                _ROUTE_STOPS.get(response.status_code),
                detail,
                None,
                started,
            )
        if loop.runs:
            self._ran(loop.runs[-1][1], started)
        return response

    def _ran(self, result: AgentRunResult, started: float) -> None:
        self.result = result
        self.stop_reason = result.stop_reason
        self.duration = time.monotonic() - started
        self._read_claims(result)
        self._print()

    def _read_claims(self, result: AgentRunResult) -> None:
        """Read the reply's claims against every call this sample has made so
        far: a thread's evidence is its earlier runs too, never its seeded
        (stored) history, whose replies are the very claims under test."""
        self.calls.extend(
            ToolCall(r.tool, r.arguments, r.result, r.error, r.refusal)
            for r in result.tool_calls
        )
        self.shown.extend(result.gathered)
        evidence = Evidence.of(self.calls, date.today(), self.shown)
        self.claims.extend((self.step_label, c) for c in read_claims(result.reply, evidence))

    def _died(
        self,
        status_code: int,
        stop_reason: str | None,
        detail: str,
        result: AgentRunResult | None,
        started: float,
    ) -> NoReturn:
        self.status_code = status_code
        self.stop_reason = stop_reason
        self.detail = detail
        self.result = result
        self.duration = time.monotonic() - started
        if result is not None:
            self._read_claims(result)
        if stop_reason == "provider_error":
            self.provider_failure = provider_failure_kind(detail)
        self._print()
        raise _RunDied(detail)

    def _print(self) -> None:
        """The per-run ``[eval]`` stats line (one per step of a thread)."""
        iterations = None if self.result is None else self.result.iterations
        step = f" step={self.step_label}" if self.step_label else ""
        print(
            f"\n[eval] scenario={self.scenario}[{self.index}]{step} seed={self.seed} "
            f"stop={self.stop_reason} iterations={iterations} "
            f"duration={self.duration or 0.0:.1f}s "
            f"schema_errors={_schema_errors(self.result, self.prompt)} "
            f"trajectory=[{_trajectory(self.result)}]"
            + (f" status={self.status_code} detail={self.detail!r}" if self.detail else "")
        )

    def record(self, outcome: Outcome, error: BaseException | None) -> dict[str, Any]:
        """This sample as the report keeps it."""
        return {
            "index": self.index,
            "outcome": str(outcome),
            "seed": self.seed,
            "status_code": self.status_code,
            "stop_reason": self.stop_reason,
            "provider_failure": self.provider_failure,
            "iterations": self.iterations,
            "seconds": None if self.duration is None else round(self.duration, 1),
            "trajectory": self.trajectory,
            "tool_calls": self.tool_calls,
            "schema_errors": self.schema_errors,
            "refusals": (
                [r for step in self.steps for r in _refusals(step.result)]
                if self.steps
                else _refusals(self.result)
            ),
            "run_id": None if self.result is None else self.result.run_id,
            "error": None if error is None else str(error),
            "detail": self.detail,
            "claims": {
                "made": len(self.claims),
                "backed": sum(c.backed for _, c in self.claims),
                "unbacked": [
                    {"step": step, "family": c.family, "said": c.said, "line": c.sentence}
                    for step, c in self.missed_claims
                ],
            },
            **({"steps": [step.record() for step in self.steps]} if self.steps else {}),
        }

    def converse(
        self,
        turns: Sequence[Turn],
        *,
        thread: Sequence[tuple[str, str]] = (),
        headers: Mapping[str, str] | None = None,
    ) -> Thread:
        """Drive a scripted thread through the conversation route (#389, #405).

        ``thread`` is stored first, through the conversations service, as
        (user, assistant) text pairs: a long history costs no model turns, and
        the route reads it back exactly as it reads a real one. Then each turn
        is posted to ``POST /api/agent/conversations/{id}/messages`` (with
        ``headers``, e.g. ``X-Agent-Actor``), metered on its own, snapshotted,
        and checked straight away. The world (projects, tasks) is the
        scenario's to seed before calling this.

        One sample is the whole thread. A failed check scores the sample a
        miss, naming the step, and the later steps never run (they would answer
        a thread already gone wrong). A step that dies (a provider death, a
        429) ends the sample through ``_died`` exactly as a one-shot run does,
        so an infrastructure death anywhere in the thread re-takes the whole
        thread rather than scoring it.
        """
        started = time.monotonic()
        with self.db() as db:
            conversation = conversations_service.create_conversation(db)
            for user_text, assistant_text in thread:
                conversations_service.append_user_message(db, conversation, user_text)
                conversations_service.append_assistant_message(
                    db, conversation, _stored_turn(assistant_text)
                )
            db.commit()
            state = Thread(conversation.id, World.read(db), len(thread))
        url = f"/api/agent/conversations/{state.conversation_id}/messages"
        try:
            with self.provider() as provider, self.client() as (client, db_session):
                loop = _RecordingLoop(provider)
                app.dependency_overrides[routes_agent.get_agent_loop] = lambda: loop
                for index, (ask, check) in enumerate(turns, start=1):
                    self.step_label = f"{index}/{len(turns)}"
                    step_started = time.monotonic()
                    runs = len(loop.runs)
                    self.post_message(
                        client, url, content=ask, headers=dict(headers or {}), loop=loop
                    )
                    if len(loop.runs) != runs + 1:
                        raise RuntimeError(f"step {index} ran {len(loop.runs) - runs} loops")
                    actor, result = loop.runs[-1]
                    # Read through the client's session (one shared connection),
                    # then end the read so the next step's writers can begin.
                    world = World.read(db_session)
                    db_session.rollback()
                    previous = state.steps[-1].world if state.steps else state.baseline
                    step = Step(
                        index=index,
                        ask=ask,
                        actor=actor,
                        result=result,
                        seconds=time.monotonic() - step_started,
                        world=world,
                        events=world.events[len(previous.events) :],
                    )
                    state.steps.append(step)
                    self.steps.append(step)
                    try:
                        check(state)
                    except AssertionError as exc:
                        raise AssertionError(f"step {index} ({ask!r}): {exc}") from exc
        finally:
            self.step_label = None
            self.duration = time.monotonic() - started
        return state


def _take(
    scenario: str, body: Callable[[Sample], None], index: int, seed: int | None
) -> tuple[Outcome, Sample, BaseException | None]:
    """One sample on a fresh DB, and what kind of sample it turned out to be."""
    sample = Sample(scenario, index, seed)
    saved = runtime.session_factory
    runtime.session_factory = sample.db
    error: BaseException | None = None
    try:
        body(sample)
        # Every check passed: what the replies told the user must be true too
        # (#407). After the body, so a sample its own checks fail names that.
        _assert_claims_backed(sample)
    except _RunDied:
        pass  # the sample says why; classify reads it
    except Exception as exc:
        # A failed assertion is the model; anything else is a bug in this
        # file (classify: HARNESS). KeyboardInterrupt is deliberately not
        # caught — Ctrl-C must abort, with finished scenarios already on disk.
        error = exc
    finally:
        runtime.session_factory = saved
        sample.engine.dispose()
    outcome = classify(
        status_code=sample.status_code,
        stop_reason=sample.stop_reason,
        error=error,
        provider_failure=sample.provider_failure,
    )
    return outcome, sample, error


def _assert_claims_backed(sample: Sample) -> None:
    """Every claim the sample's replies made, its runs back (#407): an
    unbacked one fails the sample, which counts against the floor like any
    other miss. Not a live guard: the loop never sees this reading."""
    missed = sample.missed_claims
    assert not missed, "unbacked claim " + "; ".join(
        f"[{claim.family}] {claim.said!r} in {claim.sentence!r}"
        + (f" (step {step})" if step else "")
        for step, claim in missed
    )


def _gate(scenario: str, body: Callable[[Sample], None]) -> None:
    """Sample one scenario in blocks until its count decides against its floor,
    then hold it to the verdict.

    Red takes evidence: only a Wilson upper bound *below* the floor fails. A
    count still UNDECIDED at the cap passes, flagged. An infra death is thrown
    away and re-taken without consuming a sample; a rejected request or a
    harness bug fails the scenario on the spot, with what was measured so far
    already reported.
    """
    floor = _FLOORS[scenario]
    blocks: list[tuple[int, int]] = []
    failures: list[str] = []
    samples: list[dict[str, Any]] = []
    # (schema_errors, tool_calls) per scored sample (#404).
    scored: list[tuple[int, int]] = []
    # Claims made and backed over the scored samples (#407): reported, never
    # asserted (an unbacked claim already failed its sample).
    claims = Tally()
    infra = 0
    taken = 0
    started = time.monotonic()

    def report(**meta: Any) -> None:
        write_jsonl(
            _REPORT_PATH,
            {
                "kind": "scenario",
                "seconds": round(time.monotonic() - started, 1),
                "samples": samples,
                **meta,
            },
        )

    while True:
        block_passed = 0
        block_runs = 0
        while block_runs < _BLOCK_RUNS:
            taken += 1
            # Keyed on the scored position, so an infra retry re-takes the
            # same seed and the pairs behind it stay aligned.
            seed = sample_seed(_SEED_BASE, sum(r for _, r in blocks) + block_runs)
            outcome, sample, error = _take(scenario, body, taken, seed)
            _SUITE.samples += 1
            samples.append(sample.record(outcome, error))
            if outcome is Outcome.PROVIDER_REJECTED:
                report(scenario=scenario, floor=floor, decision="PROVIDER_REJECTED", infra=infra)
                pytest.fail(
                    f"{scenario}: llama-server refused the request ({sample.detail}) — "
                    "no rate was measured, and a retry would get the same refusal."
                )
            if outcome is Outcome.INFRA:
                infra += 1
                budget_left = _SUITE.spend_infra()
                print(f"[eval]   ⟳ infra retry {infra}/{_INFRA_RETRIES} ({scenario})")
                if infra >= _INFRA_RETRIES or not budget_left:
                    report(
                        scenario=scenario,
                        floor=floor,
                        decision="INFRA_ABORTED",
                        infra=infra,
                        suite_infra_spent=_SUITE.infra_spent,
                    )
                    pytest.fail(
                        f"{scenario}: INFRA_ABORTED after {infra} provider deaths "
                        f"(suite budget spent {_SUITE.infra_spent}/{_SUITE.infra_budget}). "
                        "No rate was measured: llama-server is not staying up."
                    )
                continue
            if outcome is Outcome.HARNESS:
                report(
                    scenario=scenario,
                    floor=floor,
                    decision="HARNESS_ERROR",
                    infra=infra,
                    error=repr(error),
                )
                assert error is not None  # HARNESS is only reachable with one
                raise error
            block_runs += 1
            passed = outcome is Outcome.PASS
            sample_claims = Tally()
            for step, claim in sample.claims:
                where: dict[str, Any] = {"scenario": scenario, "sample": taken}
                if step:
                    where["step"] = step
                sample_claims.add([claim], **where)
            claims.merge(sample_claims)
            _RECORDS.append(
                RunRecord(
                    scenario,
                    passed,
                    sample.iterations,
                    sample.duration,
                    sample.tool_calls,
                    sample.schema_errors,
                    claims_made=sample_claims.scored_made,
                    claims_backed=sample_claims.scored_backed,
                )
            )
            scored.append((sample.schema_errors, sample.tool_calls))
            if passed:
                block_passed += 1
            else:
                why = error or f"HTTP {sample.status_code} {sample.stop_reason}: {sample.detail}"
                failures.append(f"run {taken}: {why}")
        blocks.append((block_passed, block_runs))
        result = RateResult.from_blocks(
            blocks=blocks, failures=failures, floor=floor, infra=infra
        )
        if result.decision is not Decision.UNDECIDED or result.runs >= _MAX_RUNS:
            break

    _SUITE.scenarios += 1
    _RESULTS[scenario] = result
    _CLAIMS.merge(claims)
    schema = schema_stats(scored)
    print(f"\n[eval] scenario={scenario} PASS_RATE={result.summary()} floor={floor:.0%}")
    print(
        f"[eval]   schema corrections {schema['schema_errors']}/{schema['tool_calls']} calls, "
        f"{schema['schema_errors_per_sample']}/sample, "
        f"{schema['samples_with_schema_errors']}/{len(scored)} samples"
    )
    print(f"[eval]   claims backed {claims.summary()}")
    for miss in claims.misses:
        print(f"[eval]   ✗ unbacked [{miss['family']}] {miss['line']!r} (sample {miss['sample']})")
    for failure in result.failures:
        print(f"[eval]   ✗ {failure}")
    for mode, count in sorted(result.failure_modes.items(), key=lambda item: -item[1]):
        print(f"[eval]   ×{count} {mode}")
    report(**scenario_record(scenario, result, floor, **schema, claims=claims.as_dict()))
    detail = f"{result.summary()} floor={floor:.0%}"
    assert result.decision is not Decision.BELOW_FLOOR, (
        f"{scenario} is below its floor with evidence, not noise: {detail}"
    )
    if result.decision is Decision.UNDECIDED:
        print(f"[eval]   ⚠ UNDECIDED at the sampling cap: {detail}")


def _event_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(ActivityEvent)).scalar_one()


# --- Scenarios ----------------------------------------------------------------


def _create_task_with_fields(sample: Sample) -> None:
    """Create with every field: title, project routing, priority, date math."""
    with sample.db() as db:
        errands = projects_service.create_project(db, name="Errands")
        errands_id = errands.id
        db.commit()

    result = sample.run(
        'Create a task "Buy stamps" in the Errands project, high priority, due tomorrow.',
    )

    assert result.stop_reason == "completed"
    assert _successful(result, "create_task"), "expected a successful create_task"
    with sample.db() as db:
        # .all() + a count rather than scalar_one(): a model that created two
        # tasks is a miss to score, not a harness error (#403).
        tasks = db.execute(select(Task).where(Task.deleted_at.is_(None))).scalars().all()
        assert len(tasks) == 1, f"expected one task, got {[t.title for t in tasks]}"
        task = tasks[0]
        assert "stamp" in task.title.lower()
        assert task.project_id == errands_id
        assert task.priority.value == "high"
        assert task.due_date == date.today() + timedelta(days=1)
        events = db.execute(
            select(ActivityEvent).where(ActivityEvent.entity_type == "task")
        ).scalars().all()
        assert events and all(e.actor == LOOP_ACTOR for e in events)


def _find_and_complete(sample: Sample) -> None:
    """Retrieval tripwire: the task is described, not named — find it, then act.
    Found means a read before the write, or the task among the matches the
    run was shown before it started (#415): either way the id is not guessed."""
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home")
        for title in (
            "Fix the leaking kitchen tap",
            "Rake the leaves",
            "Book dentist appointment",
        ):
            tasks_service.create_task(db, project_id=home.id, title=title)
        db.commit()

    result = sample.run("Mark the task about the dentist as done.")

    assert result.stop_reason == "completed"
    completes = _successful(result, "complete_task")
    assert completes, "expected a successful complete_task"
    # A read (search/list/get), or the gather, must have located the task.
    tools = [record.tool for record in result.tool_calls]
    first_complete = tools.index("complete_task")
    gathered = [row.get("title") for row in result.gathered]
    assert "Book dentist appointment" in gathered or any(
        _is_read(tool) for tool in tools[:first_complete]
    ), f"no read before complete_task in {tools}, gathered {gathered}"
    with sample.db() as db:
        tasks = {
            t.title: t.workflow_status
            for t in db.execute(select(Task)).scalars().all()
        }
        assert tasks["Book dentist appointment"] == TaskWorkflowStatus.done
        assert tasks["Fix the leaking kitchen tap"] == TaskWorkflowStatus.open
        assert tasks["Rake the leaves"] == TaskWorkflowStatus.open


def _reschedule(sample: Sample) -> None:
    """Targeted field update: change the due date, touch nothing else.

    Dates are relative to today and the target is a full ISO date, so the ask
    is unambiguous year-round (a bare "July 20" is year-ambiguous once past).
    """
    seeded_due = date.today() + timedelta(days=3)
    target_due = date.today() + timedelta(days=10)
    with sample.db() as db:
        work = projects_service.create_project(db, name="Work")
        task = tasks_service.create_task(
            db,
            project_id=work.id,
            title="Quarterly report",
            due_date=seeded_due,
        )
        task_id = task.id
        db.commit()

    result = sample.run(
        "Change the due date of the quarterly report to "
        f"{target_due.isoformat()}.",
    )

    assert result.stop_reason == "completed"
    assert _successful(result, "update_task"), "expected a successful update_task"
    with sample.db() as db:
        updated = db.get(Task, task_id)
        assert updated is not None
        assert updated.due_date == target_due
        assert updated.workflow_status == TaskWorkflowStatus.open
        assert updated.title == "Quarterly report"


def _delete_is_soft_and_restorable(sample: Sample) -> None:
    """The only delete the agent has is the trash; the row must be restorable."""
    with sample.db() as db:
        general = projects_service.create_project(db, name="Scratch")
        task = tasks_service.create_task(
            db, project_id=general.id, title="Old draft"
        )
        task_id = task.id
        project_id = general.id
        db.commit()

    result = sample.run('Delete the task "Old draft".')

    assert result.stop_reason == "completed"
    assert _successful(result, "trash_task"), "expected a successful trash_task"
    with sample.db() as db:
        # Gone from the active set, present in the trash, restorable.
        assert db.execute(
            select(Task).where(Task.deleted_at.is_(None))
        ).scalars().all() == []
        trashed = task_trash.get_deleted_task(db, task_id)
        assert trashed is not None and trashed.deleted_at is not None
        deleted_events = db.execute(
            select(ActivityEvent).where(ActivityEvent.action == "deleted")
        ).scalars().all()
        assert deleted_events and all(e.actor == LOOP_ACTOR for e in deleted_events)

    # "Restorable" for real: restore through the service the restore_task tool
    # calls, then check the task is whole again and the restore was audited.
    with sample.db() as db:
        trashed = task_trash.get_deleted_task(db, task_id)
        assert trashed is not None
        task_trash.restore_task(db, trashed)
        db.commit()
    with sample.db() as db:
        active_tasks = db.execute(
            select(Task).where(Task.deleted_at.is_(None))
        ).scalars().all()
        assert [t.id for t in active_tasks] == [task_id]
        restored = active_tasks[0]
        assert restored.deleted_at is None
        assert restored.title == "Old draft"
        assert restored.project_id == project_id
        assert task_trash.get_deleted_task(db, task_id) is None
        restored_events = db.execute(
            select(ActivityEvent).where(
                ActivityEvent.entity_type == "task",
                ActivityEvent.entity_id == task_id,
                ActivityEvent.action == "restored",
            )
        ).scalars().all()
        assert len(restored_events) == 1


# Numbers a reply states, digits or spelled out (temp 1.0 may write "three").
_NUMBER_WORDS = {
    word: value
    for value, word in enumerate(
        "zero one two three four five six seven eight nine ten".split()
    )
}
_NUMBER_RE = re.compile(r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)


def _numbers_in(reply: str) -> list[int]:
    """Every integer the reply states, in order — "13" is 13, never 3 (#381)."""
    return [
        int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]
        for token in _NUMBER_RE.findall(reply)
    ]


def _read_only_question_mutates_nothing(sample: Sample) -> None:
    """A question is answered with reads: correct number, zero writes."""
    with sample.db() as db:
        reading = projects_service.create_project(db, name="Reading")
        for title in ("Dune", "Middlemarch", "The Windup Bird Chronicle"):
            tasks_service.create_task(db, project_id=reading.id, title=title)
        done = tasks_service.create_task(db, project_id=reading.id, title="Emma")
        tasks_service.mark_done(db, done)
        db.commit()
        events_before = _event_count(db)

    result = sample.run(
        "How many open tasks are in the Reading project? Reply with just the number.",
    )

    assert result.stop_reason == "completed"
    assert result.reply is not None
    # The count must be the only number stated: a substring check passed
    # "13", "30", and "3 of 4 are done" (#381).
    assert _numbers_in(result.reply) == [3], f"wrong count: {result.reply!r}"
    assert _mutations(result) == [], (
        f"read-only ask must not mutate: {[r.tool for r in _mutations(result)]}"
    )
    with sample.db() as db:
        assert _event_count(db) == events_before


# honest_about_missing frugality ceilings (#329); see the asserts' comment.
_MISSING_MAX_ITERATIONS = 7
_MISSING_MAX_READS = 6


def _honest_about_missing_task(sample: Sample) -> None:
    """A target that doesn't exist must not be invented or acted on."""
    with sample.db() as db:
        projects_service.create_project(db, name="Hangar")
        db.commit()
        events_before = _event_count(db)

    # The loop's own cap stays loose (14) so an over-searching run still
    # completes and fails the frugality asserts below with its trajectory,
    # rather than dying as a bare max_iterations stop.
    result = sample.run(
        'Mark the task "Launch the zeppelin" as done.',
        max_iterations=14,
    )

    assert result.stop_reason == "completed"
    assert result.reply, "expected a text reply explaining the situation"
    # No writes alone would pass "Done! I marked it complete." (#383). The
    # concession's vocabulary is the claim reader's not-found family
    # (``claims.NOT_FOUND``), which also checks, in every scenario, that a
    # "couldn't find" is true (#407).
    assert concedes_miss(result.reply), (
        f"reply doesn't concede the task is missing: {result.reply!r}"
    )
    # Nothing was completed, created, or otherwise written.
    assert not _successful(result, "complete_task")
    assert not _successful(result, "create_task")
    with sample.db() as db:
        assert (
            db.execute(select(func.count()).select_from(Task)).scalar_one() == 0
        )
        assert _event_count(db) == events_before
        # And no project was invented either.
        assert (
            db.execute(select(func.count()).select_from(Project)).scalar_one() == 1
        )

    # Search-frugality tripwire (#329). Under llama-swap (2026-10-18, 32
    # runs) the concession takes 2-5 iterations / 1-4 reads (search, then up
    # to three list_tasks or a list_trash); the pre-llama-swap baseline
    # re-checked exhaustively (per-project lists, the trash) for up to 10
    # turns. The ceilings leave 2 of headroom over the observed maximum and
    # still trip well before a return to that sweep. Reads are capped
    # separately so parallel tool calls can't hide a sweep in few turns.
    tools = [record.tool for record in result.tool_calls]
    assert result.iterations <= _MISSING_MAX_ITERATIONS, (
        f"over-searched: {result.iterations} iterations "
        f"(ceiling {_MISSING_MAX_ITERATIONS}) via {tools}"
    )
    reads = [tool for tool in tools if _is_read(tool)]
    assert len(reads) <= _MISSING_MAX_READS, (
        f"over-searched: {len(reads)} read calls "
        f"(ceiling {_MISSING_MAX_READS}) via {tools}"
    )


def _delegate_actor_attribution(sample: Sample) -> None:
    """A conductor-delegated run is audited as the conductor, end to end (#328).

    Driven through ``POST /api/agent/conversations/{id}/messages`` with the
    ``X-Agent-Actor`` header — the only place a delegate identity enters — so
    header → ``resolve_actor`` → loop → registry → ``activity_events`` is
    exercised against the real model, not just a scripted provider.

    Once the client is in play, reads go through its session (the session the
    client's routes use): the sample's engine is one shared StaticPool
    connection, and a second session would BEGIN inside its open transaction.
    """
    delegate = "agent:conductor"
    assert delegate in DELEGATE_ACTORS
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home")
        home_id = home.id
        db.commit()

    with sample.provider() as provider, sample.client() as (client, db_session):
        loop = _RecordingLoop(provider)
        app.dependency_overrides[routes_agent.get_agent_loop] = lambda: loop
        # A user-made conversation (no header): its own audit row stays the user's.
        conversation_id = client.post("/api/agent/conversations", json={}).json()["id"]
        last_event_before = db_session.execute(
            select(func.max(ActivityEvent.id))
        ).scalar_one()
        db_session.rollback()  # end the read; the run's tool sessions share the connection

        response = sample.post_message(
            client,
            f"/api/agent/conversations/{conversation_id}/messages",
            content='Create a task "Call the plumber" in the Home project.',
            headers={"X-Agent-Actor": delegate},
            loop=loop,
        )

        assert len(loop.runs) == 1
        actor, result = loop.runs[0]
        # The header was resolved to the delegate before the loop ran.
        assert actor == delegate
        assert result.stop_reason == "completed"
        assert _successful(result, "create_task"), "expected a successful create_task"
        # The persisted assistant turn is the run's trajectory, as returned.
        assistant = response.json()["assistant_message"]
        assert assistant["stop_reason"] == "completed"
        assert [c["tool"] for c in assistant["tool_calls"] or []] == [
            r.tool for r in result.tool_calls
        ]

        tasks = db_session.execute(
            select(Task).where(Task.deleted_at.is_(None))
        ).scalars().all()
        assert len(tasks) == 1, f"expected one task, got {[t.title for t in tasks]}"
        task = tasks[0]
        assert "plumber" in task.title.lower()
        assert task.project_id == home_id
        # Every event the run wrote is the delegate's — never the loop's own
        # identity, the MCP default, or the (null) user.
        run_events = db_session.execute(
            select(ActivityEvent).where(ActivityEvent.id > last_event_before)
        ).scalars().all()
        assert run_events, "the run wrote no activity events"
        assert all(e.actor == delegate for e in run_events), [
            (e.entity_type, e.action, e.actor) for e in run_events
        ]
        assert any(
            e.entity_type == "task" and e.action == "created" and e.entity_id == task.id
            for e in run_events
        )
        # Writes outside the delegated run are not re-attributed: the
        # conversation's creation (and the seed) stay the user's.
        prior = db_session.execute(
            select(ActivityEvent).where(ActivityEvent.id <= last_event_before)
        ).scalars().all()
        assert prior and all(e.actor is None for e in prior)


def _events_after(db: Session, last_id: int | None) -> list[ActivityEvent]:
    """The activity rows written after ``last_id`` (the seed's last), oldest first."""
    query = select(ActivityEvent).order_by(ActivityEvent.id)
    if last_id is not None:
        query = query.where(ActivityEvent.id > last_id)
    return list(db.execute(query).scalars().all())


def _last_event_id(db: Session) -> int | None:
    return db.execute(select(func.max(ActivityEvent.id))).scalar_one()


def _assert_run_events_are_the_loops(db: Session, last_id: int | None) -> None:
    """The run wrote something, and every row of it is the loop's."""
    events = _events_after(db, last_id)
    assert events, "the run wrote no activity events"
    assert all(e.actor == LOOP_ACTOR for e in events), [
        (e.entity_type, e.action, e.actor) for e in events
    ]


def _focus_pin_and_commitment(sample: Sample) -> None:
    """A timed pin and a one-off commitment in one ask, both on today (#378)."""
    today = date.today()
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home")
        dentist = tasks_service.create_task(
            db, project_id=home.id, title="Book dentist appointment"
        )
        tasks_service.create_task(db, project_id=home.id, title="Rake the leaves")
        dentist_id = dentist.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run(
        "Pin the dentist task to my day at 2pm, and block 12 to 1 for lunch."
    )

    assert result.stop_reason == "completed"
    assert _successful(result, "pin_focus_task"), "expected a successful pin_focus_task"
    assert _successful(result, "add_focus_commitment"), (
        "expected a successful add_focus_commitment"
    )
    with sample.db() as db:
        entries = focus_plan_service.list_entries(db, today)
        pins = [e for e in entries if e.task_id is not None]
        commitments = [e for e in entries if e.task_id is None]
        assert [(p.task_id, p.start_minute) for p in pins] == [(dentist_id, 14 * 60)], (
            f"pins: {[(p.task_id, p.start_minute) for p in pins]}"
        )
        assert len(commitments) == 1, f"commitments: {[c.title for c in commitments]}"
        lunch = commitments[0]
        assert lunch.title is not None and "lunch" in lunch.title.lower()
        assert (lunch.start_minute, lunch.duration_minutes) == (12 * 60, 60)
        assert lunch.repeat_interval is None, "a one-off lunch block must not repeat"
        # Nothing landed on another day either.
        all_live = db.execute(
            select(FocusPlanEntry).where(FocusPlanEntry.deleted_at.is_(None))
        ).scalars().all()
        assert len(all_live) == 2, [(e.plan_date, e.title, e.task_id) for e in all_live]
        _assert_run_events_are_the_loops(db, last_seed_event)


def _focus_remove_is_soft(sample: Sample) -> None:
    """Dropping a commitment soft-deletes it, and it comes back whole (#378)."""
    today = date.today()
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home")
        dentist = tasks_service.create_task(
            db, project_id=home.id, title="Book dentist appointment"
        )
        pin = focus_plan_service.pin_task(
            db, plan_date=today, task=dentist, start_minute=14 * 60
        )
        lunch = focus_plan_service.add_commitment(
            db, plan_date=today, title="Lunch", start_minute=12 * 60, duration_minutes=60
        )
        pin_id, lunch_id = pin.id, lunch.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run("Take lunch off my plan for today.")

    assert result.stop_reason == "completed"
    assert _successful(result, "remove_focus_entry"), (
        "expected a successful remove_focus_entry"
    )
    with sample.db() as db:
        assert [e.id for e in focus_plan_service.list_entries(db, today)] == [pin_id]
        removed = focus_plan_service.get_deleted_entry(db, lunch_id)
        assert removed is not None, "lunch should be soft-deleted, not gone"
        _assert_run_events_are_the_loops(db, last_seed_event)

    # Restorable for real, through the service restore_focus_entry calls.
    with sample.db() as db:
        removed = focus_plan_service.get_deleted_entry(db, lunch_id)
        assert removed is not None
        focus_plan_service.restore_entry(db, removed)
        db.commit()
    with sample.db() as db:
        restored = focus_plan_service.get_entry(db, lunch_id)
        assert restored is not None
        assert (restored.title, restored.start_minute, restored.duration_minutes) == (
            "Lunch",
            12 * 60,
            60,
        )


def _focus_skip_series_not_task(sample: Sample) -> None:
    """Skip today's standup commitment, not the recurring task beside it (#378).

    ``skip_focus_occurrence`` (a Focus series) and ``skip_occurrence`` (a
    recurring task) sit side by side in the tool list since #426; the
    distractor is a weekly task whose title also says "standup".
    """
    today = date.today()
    with sample.db() as db:
        work = projects_service.create_project(db, name="Work")
        notes = tasks_service.create_task(
            db, project_id=work.id, title="Prepare standup notes", due_date=today
        )
        tasks_service.update_task(db, notes, {"repeat_interval": {"unit": "week", "every": 1}})
        standup = focus_plan_service.add_commitment(
            db,
            plan_date=today - timedelta(days=7),
            title="Team standup",
            start_minute=9 * 60 + 30,
            duration_minutes=15,
            repeat_interval={"unit": "day", "every": 1},
        )
        notes_id, standup_id = notes.id, standup.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run("Skip today's team standup, it's cancelled.")

    assert result.stop_reason == "completed"
    skips = _successful(result, "skip_focus_occurrence")
    assert skips, "expected a successful skip_focus_occurrence"
    assert not [r for r in result.tool_calls if r.tool == "skip_occurrence"], (
        "reached for the recurring-task skip"
    )
    with sample.db() as db:
        skipped = focus_plan_service.day_skipped_commitments(db, today)
        assert [series.id for series, _ in skipped] == [standup_id]
        series = focus_plan_service.get_entry(db, standup_id)
        assert series is not None and series.stop_date is None, "the series must go on"
        # Only today: tomorrow's standup still happens.
        tomorrow = focus_plan_service.day_commitments(db, today + timedelta(days=1))
        assert len(tomorrow) == 1
        task = db.get(Task, notes_id)
        assert task is not None and task.deleted_at is None
        assert task.due_date == today and task.repeat_interval is not None
        _assert_run_events_are_the_loops(db, last_seed_event)


def _dependency_add(sample: Sample) -> None:
    # Titles are named as stored: paraphrase retrieval ("painting", "buying")
    # is covered by dependency_paraphrase (#444).
    _dependency_edge(sample, '"Paint the walls" is blocked by "Buy primer".')


def _dependency_edge(sample: Sample, ask: str) -> None:
    """"X is blocked by Y": one edge, pointing the right way (#385).

    Direction is the trap: ``add_dependency(task_id, depends_on_task_id)``
    makes ``task_id`` wait, so the blocked task is the first argument.
    """
    with sample.db() as db:
        garage = projects_service.create_project(db, name="Garage")
        paint = tasks_service.create_task(db, project_id=garage.id, title="Paint the walls")
        primer = tasks_service.create_task(db, project_id=garage.id, title="Buy primer")
        tasks_service.create_task(db, project_id=garage.id, title="Sweep the floor")
        paint_id, primer_id = paint.id, primer.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run(ask)

    assert result.stop_reason == "completed"
    assert _successful(result, "add_dependency"), "expected a successful add_dependency"
    with sample.db() as db:
        edges = db.execute(
            select(TaskDependency).where(TaskDependency.deleted_at.is_(None))
        ).scalars().all()
        assert [(e.task_id, e.depends_on_task_id) for e in edges] == [
            (paint_id, primer_id)
        ], f"edges (waiter, blocker): {[(e.task_id, e.depends_on_task_id) for e in edges]}"
        assert deps_service.is_blocked(db, paint_id)
        assert not deps_service.is_blocked(db, primer_id)
        _assert_run_events_are_the_loops(db, last_seed_event)


def _dependency_remove(sample: Sample) -> None:
    """Drop one of two blockers; the edge is soft-deleted, the other stays (#385)."""
    with sample.db() as db:
        garage = projects_service.create_project(db, name="Garage")
        paint = tasks_service.create_task(db, project_id=garage.id, title="Paint the walls")
        primer = tasks_service.create_task(db, project_id=garage.id, title="Buy primer")
        sweep = tasks_service.create_task(db, project_id=garage.id, title="Sweep the floor")
        primer_edge = deps_service.add_dependency(db, paint.id, primer.id)
        sweep_edge = deps_service.add_dependency(db, paint.id, sweep.id)
        paint_id, primer_edge_id, sweep_edge_id = paint.id, primer_edge.id, sweep_edge.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run(
        '"Paint the walls" doesn\'t need to wait on "Sweep the floor" any more.'
    )

    assert result.stop_reason == "completed"
    assert _successful(result, "remove_dependency"), (
        "expected a successful remove_dependency"
    )
    with sample.db() as db:
        assert [e.id for e in deps_service.list_dependencies(db, paint_id)] == [
            primer_edge_id
        ]
        removed = db.get(TaskDependency, sweep_edge_id)
        assert removed is not None and removed.deleted_at is not None, (
            "the edge must be soft-deleted, not gone"
        )
        assert db.execute(
            select(func.count()).select_from(Task).where(Task.deleted_at.is_(None))
        ).scalar_one() == 3, "no task may be trashed to drop a dependency"
        _assert_run_events_are_the_loops(db, last_seed_event)


_WEEKLY = {"unit": "week", "every": 1}


def _seed_weekly(sample: Sample, title: str) -> tuple[int, int | None]:
    """A weekly task due today in a Home project; returns its id and the last
    seed event."""
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home")
        task = tasks_service.create_task(
            db, project_id=home.id, title=title, due_date=date.today()
        )
        tasks_service.update_task(db, task, {"repeat_interval": _WEEKLY})
        tasks_service.create_task(db, project_id=home.id, title="Rake the leaves")
        task_id = task.id
        db.commit()
        return task_id, _last_event_id(db)


def _live_tasks(db: Session) -> list[Task]:
    return list(
        db.execute(select(Task).where(Task.deleted_at.is_(None)).order_by(Task.id))
        .scalars()
        .all()
    )


def _recurrence_make_repeating(sample: Sample) -> None:
    """Attach a weekly cadence to an existing task, nothing else (#386)."""
    due = date.today() + timedelta(days=2)
    with sample.db() as db:
        work = projects_service.create_project(db, name="Work")
        review = tasks_service.create_task(
            db, project_id=work.id, title="Weekly review", due_date=due
        )
        review_id = review.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run("Make the weekly review repeat every week.")

    assert result.stop_reason == "completed"
    assert _successful(result, "update_task"), "expected a successful update_task"
    with sample.db() as db:
        tasks = _live_tasks(db)
        assert [t.id for t in tasks] == [review_id], [t.title for t in tasks]
        review = tasks[0]
        assert review.repeat_interval == _WEEKLY, review.repeat_interval
        assert review.due_date == due, "the due date must not move"
        assert review.workflow_status == TaskWorkflowStatus.open
        _assert_run_events_are_the_loops(db, last_seed_event)


def _recurrence_skip(sample: Sample) -> None:
    """Skip this week's occurrence: it goes to the trash, next week's is live (#386)."""
    today = date.today()
    task_id, last_seed_event = _seed_weekly(sample, "Water the plants")

    result = sample.run('Skip this week\'s "Water the plants".')

    assert result.stop_reason == "completed"
    assert _successful(result, "skip_occurrence"), "expected a successful skip_occurrence"
    with sample.db() as db:
        skipped = task_trash.get_deleted_task(db, task_id)
        assert skipped is not None, "the skipped occurrence should be in the trash"
        plants = [t for t in _live_tasks(db) if t.title == "Water the plants"]
        assert len(plants) == 1, [(t.id, t.due_date) for t in plants]
        assert plants[0].due_date == today + timedelta(days=7)
        assert plants[0].repeat_interval == _WEEKLY, "the series must go on"
        assert plants[0].workflow_status == TaskWorkflowStatus.open
        assert db.execute(
            select(func.count()).select_from(Task).where(
                Task.workflow_status == TaskWorkflowStatus.done
            )
        ).scalar_one() == 0, "a skip is not a completion"
        _assert_run_events_are_the_loops(db, last_seed_event)


def _recurrence_stop(sample: Sample) -> None:
    """Stop the series: the current occurrence stays as a plain open task (#386).

    ``stop_recurrence`` is the specific tool; ``update_task`` clearing
    ``repeat_interval`` reaches the same end state on a one-row series, so the
    end state is what's asserted.
    """
    today = date.today()
    task_id, last_seed_event = _seed_weekly(sample, "Water the plants")

    result = sample.run('Stop "Water the plants" from repeating.')

    assert result.stop_reason == "completed"
    assert _successful(result, "stop_recurrence") or _successful(result, "update_task"), (
        "expected a successful stop_recurrence (or update_task)"
    )
    with sample.db() as db:
        tasks = _live_tasks(db)
        assert sorted(t.title for t in tasks) == ["Rake the leaves", "Water the plants"]
        plants = db.get(Task, task_id)
        assert plants is not None and plants.deleted_at is None
        assert plants.repeat_interval is None, plants.repeat_interval
        assert plants.due_date == today, "stopping must not move or clear the date"
        assert plants.workflow_status == TaskWorkflowStatus.open
        _assert_run_events_are_the_loops(db, last_seed_event)


def _restore_task_from_trash(sample: Sample) -> None:
    """"Bring back the task I deleted" goes through list_trash → restore_task (#387).

    Search only sees live rows, so the trash is the only place to look. A
    second trashed task is the distractor; it must stay in the trash.
    """
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home")
        filters = tasks_service.create_task(
            db, project_id=home.id, title="Order new furnace filters"
        )
        gutters = tasks_service.create_task(db, project_id=home.id, title="Clean the gutters")
        tasks_service.create_task(db, project_id=home.id, title="Rake the leaves")
        tasks_service.soft_delete_task(db, filters)
        tasks_service.soft_delete_task(db, gutters)
        filters_id, gutters_id, home_id = filters.id, gutters.id, home.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run(
        "I deleted the task about ordering furnace filters by mistake. Bring it back."
    )

    assert result.stop_reason == "completed"
    assert _successful(result, "restore_task"), "expected a successful restore_task"
    with sample.db() as db:
        assert sorted(t.title for t in _live_tasks(db)) == [
            "Order new furnace filters",
            "Rake the leaves",
        ]
        restored = db.get(Task, filters_id)
        assert restored is not None and restored.deleted_at is None
        assert restored.project_id == home_id
        assert task_trash.get_deleted_task(db, gutters_id) is not None, (
            "the other trashed task must stay in the trash"
        )
        events = _events_after(db, last_seed_event)
        # One restore, and nothing else written: no re-create of the task.
        assert [(e.entity_type, e.entity_id, e.action) for e in events] == [
            ("task", filters_id, "restored")
        ], [(e.entity_type, e.entity_id, e.action) for e in events]
        assert all(e.actor == LOOP_ACTOR for e in events)


def _restore_project_from_trash(sample: Sample) -> None:
    """Restore a trashed project together with the tasks trashed with it (#387)."""
    with sample.db() as db:
        garage = projects_service.create_project(db, name="Garage")
        for title in ("Paint the walls", "Buy primer"):
            tasks_service.create_task(db, project_id=garage.id, title=title)
        projects_service.create_project(db, name="Home")
        projects_service.soft_delete_project(db, garage)
        garage_id = garage.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run("Restore the Garage project I deleted, with its tasks.")

    assert result.stop_reason == "completed"
    assert _successful(result, "restore_project"), "expected a successful restore_project"
    with sample.db() as db:
        projects = db.execute(
            select(Project).where(Project.deleted_at.is_(None)).order_by(Project.name)
        ).scalars().all()
        assert [p.name for p in projects] == ["Garage", "Home"], [p.name for p in projects]
        assert projects[0].id == garage_id, "restored, not re-created"
        tasks = _live_tasks(db)
        assert sorted(t.title for t in tasks) == ["Buy primer", "Paint the walls"]
        assert all(t.project_id == garage_id for t in tasks)
        _assert_run_events_are_the_loops(db, last_seed_event)


def _live_projects(db: Session) -> list[Project]:
    return list(
        db.execute(select(Project).where(Project.deleted_at.is_(None)).order_by(Project.id))
        .scalars()
        .all()
    )


def _seed_garage(sample: Sample, *, closed: bool = False) -> tuple[int, int | None]:
    """Garage (two open tasks) beside Home; returns Garage's id and the last
    seed event."""
    with sample.db() as db:
        garage = projects_service.create_project(db, name="Garage")
        for title in ("Paint the walls", "Buy primer"):
            tasks_service.create_task(db, project_id=garage.id, title=title)
        projects_service.create_project(db, name="Home")
        if closed:
            projects_service.close_project(db, garage)
        garage_id = garage.id
        db.commit()
        return garage_id, _last_event_id(db)


def _project_create(sample: Sample) -> None:
    """Create one project, no tasks with it (#388)."""
    with sample.db() as db:
        projects_service.create_project(db, name="Home")
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run(
        'Start a new project called "Kitchen remodel" for redoing the cabinets and counters.'
    )

    assert result.stop_reason == "completed"
    assert _successful(result, "create_project"), "expected a successful create_project"
    with sample.db() as db:
        names = [p.name for p in _live_projects(db)]
        assert len(names) == 2 and names[0] == "Home", names
        assert names[1].lower() == "kitchen remodel", names
        assert db.execute(select(func.count()).select_from(Task)).scalar_one() == 0, (
            "the ask made a project, not tasks"
        )
        _assert_run_events_are_the_loops(db, last_seed_event)


def _project_rename(sample: Sample) -> None:
    """Rename in place: same id, new name, tasks still in it (#388)."""
    garage_id, last_seed_event = _seed_garage(sample)

    result = sample.run('Rename the Garage project to "Workshop".')

    assert result.stop_reason == "completed"
    assert _successful(result, "update_project"), "expected a successful update_project"
    with sample.db() as db:
        projects = {p.id: p.name for p in _live_projects(db)}
        assert projects[garage_id] == "Workshop", projects
        assert sorted(projects.values()) == ["Home", "Workshop"]
        assert all(t.project_id == garage_id for t in _live_tasks(db))
        _assert_run_events_are_the_loops(db, last_seed_event)


def _project_close(sample: Sample) -> None:
    """"We're done with it" closes, never trashes, and leaves the tasks alone (#388)."""
    garage_id, last_seed_event = _seed_garage(sample)

    result = sample.run("We're done with the Garage project for now, close it.")

    assert result.stop_reason == "completed"
    assert _successful(result, "close_project"), "expected a successful close_project"
    with sample.db() as db:
        garage = db.get(Project, garage_id)
        assert garage is not None
        assert garage.closed_at is not None
        assert garage.deleted_at is None, "closing is not trashing"
        tasks = _live_tasks(db)
        assert len(tasks) == 2 and all(
            t.workflow_status == TaskWorkflowStatus.open for t in tasks
        ), "closing a project leaves its tasks as they were"
        _assert_run_events_are_the_loops(db, last_seed_event)


def _project_reopen(sample: Sample) -> None:
    """Reopen a closed project; list_projects hides it by default (#388)."""
    garage_id, last_seed_event = _seed_garage(sample, closed=True)

    result = sample.run("Reopen the Garage project.")

    assert result.stop_reason == "completed"
    assert _successful(result, "reopen_project"), "expected a successful reopen_project"
    with sample.db() as db:
        garage = db.get(Project, garage_id)
        assert garage is not None and garage.closed_at is None
        assert [p.name for p in _live_projects(db)] == ["Garage", "Home"], (
            "reopened, not re-created"
        )
        _assert_run_events_are_the_loops(db, last_seed_event)


def _project_trash(sample: Sample) -> None:
    """Deleting a project trashes it with its tasks, restorable; Home untouched (#388)."""
    garage_id, last_seed_event = _seed_garage(sample)

    result = sample.run("Delete the Garage project.")

    assert result.stop_reason == "completed"
    assert _successful(result, "trash_project"), "expected a successful trash_project"
    with sample.db() as db:
        assert [p.name for p in _live_projects(db)] == ["Home"]
        assert projects_service.get_deleted_project(db, garage_id) is not None
        assert _live_tasks(db) == [], "the project's tasks go to the trash with it"
        assert projects_service.count_tasks_deleted_with_project(db, garage_id) == 2
        _assert_run_events_are_the_loops(db, last_seed_event)


# --- The deployed misfires (#404) ----------------------------------------------
#
# Each scenario below replays a failure the deployed agent's run history shows
# (82 runs since 2026-07-11): ``name`` sent for a task's ``title``, calls
# double-wrapped as ``data.data``, integer priorities, ``parent_id`` for
# ``parent_task_id``, guessed ids ("Project 1 not found", "Task 291 not
# found"), and ``complete_task`` on a parent refused four times running. They
# are phase 4's before-numbers (#411 schemas, #412 state block, #413
# refusals), so the schema-correction count every sample now reports matters
# here as much as the verdict.


def _seed_projects(db: Session, *names: str, trashed_first: bool = True) -> dict[str, int]:
    """Projects by name → id. A trashed "Old stuff" takes id 1 first, so a
    guessed ``project_id: 1`` gets the deployed "Project 1 not found" rather
    than landing somewhere plausible by luck."""
    if trashed_first:
        old = projects_service.create_project(db, name="Old stuff")
        projects_service.soft_delete_project(db, old)
    ids = {name: projects_service.create_project(db, name=name).id for name in names}
    db.flush()
    return ids


_GARAGE_BATCH = (
    ("Sweep the floor", "sweep"),
    ("Hang the pegboard", "pegboard"),
    ("Sort the screws", "screw"),
    ("Fix the door hinge", "hinge"),
    ("Recycle the old paint cans", "paint can"),
)


def _batch_create(sample: Sample) -> None:
    """"Add these five tasks to Garage": five creates, all in Garage (#404).

    Five creates in one run is where the deployed history's ``name``-for-
    ``title`` and ``data.data`` refusals pile up, and two runs died at
    ``correction_limit``. Garage is not project 1, and a task already in it
    must be left alone.
    """
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Work", "Garage")
        existing = tasks_service.create_task(
            db, project_id=ids["Garage"], title="Paint the walls"
        )
        tasks_service.create_task(db, project_id=ids["Home"], title="Rake the leaves")
        garage_id, existing_id = ids["Garage"], existing.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run(
        "Add these five tasks to Garage: sweep the floor, hang the pegboard, "
        "sort the screws, fix the door hinge, recycle the old paint cans."
    )

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    with sample.db() as db:
        new = [t for t in _live_tasks(db) if t.id > existing_id and t.title != "Rake the leaves"]
        titles = [t.title for t in new]
        assert len(new) == 5, f"expected five new tasks, got {titles}"
        for _, keyword in _GARAGE_BATCH:
            assert sum(keyword in title.lower() for title in titles) == 1, (
                f"{keyword!r} should be in exactly one new title: {titles}"
            )
        assert all(t.project_id == garage_id for t in new), [
            (t.title, t.project_id) for t in new
        ]
        assert all(t.parent_task_id is None for t in new), "a batch is five tasks, not subtasks"
        assert len(_live_projects(db)) == 3, "no project invented"
        untouched = db.get(Task, existing_id)
        assert untouched is not None and untouched.title == "Paint the walls"
        # Everything the run wrote is about the five (a create then a fix-up
        # move is fine; touching another task or project is not).
        events = _events_after(db, last_seed_event)
        new_ids = {t.id for t in new}
        assert all(e.entity_type == "task" and e.entity_id in new_ids for e in events), [
            (e.entity_type, e.entity_id, e.action) for e in events
        ]
        assert all(e.actor == LOOP_ACTOR for e in events)


def _create_in_named_project(sample: Sample) -> None:
    """A create that names its project among five: routed by lookup, never by a
    guessed id (#404). Reading is id 6; id 1 is a trashed project."""
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Work", "Errands", "Garage", "Reading")
        reading_id = ids["Reading"]
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run('Add a task "Renew the library card" to my Reading project.')

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "create_task"), "expected a successful create_task"
    with sample.db() as db:
        tasks = _live_tasks(db)
        assert len(tasks) == 1, f"expected one task, got {[t.title for t in tasks]}"
        assert "library card" in tasks[0].title.lower(), tasks[0].title
        assert tasks[0].project_id == reading_id, (
            f"filed in project {tasks[0].project_id}, not Reading ({reading_id})"
        )
        assert len(_live_projects(db)) == 5, "no project invented (nor a General default)"
        _assert_run_events_are_the_loops(db, last_seed_event)


def _subtask_under_named_parent(sample: Sample) -> None:
    """A subtask under a parent named by title: ``parent_task_id`` right (#404).

    The deployed model sent ``parent_id``. The distractor parent shares the
    title's shape ("Clean out the gutters") in another project.
    """
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Garage")
        tasks_service.create_task(db, project_id=ids["Home"], title="Clean out the gutters")
        parent = tasks_service.create_task(
            db, project_id=ids["Garage"], title="Clean out the garage"
        )
        tasks_service.create_task(db, project_id=ids["Garage"], title="Paint the walls")
        parent_id, garage_id = parent.id, ids["Garage"]
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run('Add a subtask "Sort the recycling" under "Clean out the garage".')

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "create_task"), "expected a successful create_task"
    with sample.db() as db:
        new = [t for t in _live_tasks(db) if t.id > parent_id and t.title != "Paint the walls"]
        assert len(new) == 1, f"expected one new task, got {[t.title for t in new]}"
        subtask = new[0]
        assert "recycling" in subtask.title.lower(), subtask.title
        assert subtask.parent_task_id == parent_id, (
            f"parent_task_id={subtask.parent_task_id}, want {parent_id}"
        )
        # Create-then-reparent (``update_task`` setting ``parent_task_id``)
        # reaches the same end state and passes; dropping ``project_id`` on
        # the retry files it in an invented General project and doesn't.
        assert subtask.project_id == garage_id, (
            f"filed in project {subtask.project_id}, not its parent's ({garage_id})"
        )
        assert len(_live_projects(db)) == 2, "no project invented (nor a General default)"
        assert len(_live_tasks(db)) == 4
        _assert_run_events_are_the_loops(db, last_seed_event)


# The reply names what stands in the way (#404's "held" branch).
_MENTIONS_SUBTASKS_RE = re.compile(r"\bsub-?tasks?\b", re.IGNORECASE)

_GARAGE_SUBTASKS = ("Sweep the floor", "Sort the screws", "Haul boxes to the dump")


def _complete_parent_with_open_subtasks(sample: Sample) -> None:
    """"Mark the parent done" while its three subtasks are open (#404).

    The service never lets a parent be completed directly: its ``done`` is
    derived, and ``complete_task`` on it is refused with "This task is
    completed by its subtasks; complete them to complete it". The deployed
    agent hit that refusal four times in a row with no way forward.

    Two end states are honest, and either passes:

    * **done through the subtasks** — every subtask completed, so the parent
      reads done (its rollup). The user said the whole job is done and the
      refusal names this as the way, so it fulfils the ask rather than fakes it.
    * **held and explained** — nothing written, and the reply tells the user
      the subtasks are what stand in the way.

    Anything between fails: some subtasks done but not all (the parent half
    done, the reply free to claim otherwise), the parent's own status written
    some other way, anything trashed, or a run that loops on the refusal until
    a limit stops it (the stall rule ends a verbatim repeat as ``no_progress``).
    That last one is the deployed failure, and it survives the first branch:
    with every subtask done, a retried ``complete_task`` on the parent is
    refused with the same words, and the limit summary then says the parent
    is still pending when it reads done.
    """
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Garage")
        parent = tasks_service.create_task(
            db, project_id=ids["Garage"], title="Clean out the garage"
        )
        subtask_ids = [
            tasks_service.create_task(
                db, project_id=ids["Garage"], title=title, parent_task_id=parent.id
            ).id
            for title in _GARAGE_SUBTASKS
        ]
        tasks_service.create_task(db, project_id=ids["Home"], title="Rake the leaves")
        parent_id = parent.id
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run('Mark "Clean out the garage" as done.')

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    with sample.db() as db:
        assert len(_live_tasks(db)) == 5, "nothing may be trashed"
        subtasks = [db.get(Task, task_id) for task_id in subtask_ids]
        done = [t.title for t in subtasks if t and t.workflow_status == TaskWorkflowStatus.done]
        stored = db.get(Task, parent_id)
        assert stored is not None
        events = _events_after(db, last_seed_event)
        if done:
            assert len(done) == len(_GARAGE_SUBTASKS), f"only some subtasks completed: {done}"
            assert tasks_service.get_rollup(db, stored).workflow_status == (
                TaskWorkflowStatus.done
            ), "every subtask is done, so the parent must read done"
            trail = [(e.entity_type, e.entity_id, e.action) for e in events]
            # Completions only, of the subtasks (and the parent, should a
            # rollup ever log one); nothing else written on the way.
            family = {*subtask_ids, parent_id}
            assert all(
                (e.entity_type, e.action) == ("task", "completed") and e.entity_id in family
                for e in events
            ), trail
            assert {e.entity_id for e in events} >= set(subtask_ids), trail
            assert all(e.actor == LOOP_ACTOR for e in events)
        else:
            assert events == [], [(e.entity_type, e.entity_id, e.action) for e in events]
            assert stored.workflow_status == TaskWorkflowStatus.open
            assert result.reply and _MENTIONS_SUBTASKS_RE.search(result.reply), (
                f"held without saying the subtasks are why: {result.reply!r}"
            )


_Q3_REPORTS = (
    "Draft the Q3 report",
    "Review the Q3 report",
    "Send the Q3 report to finance",
)


def _edit_among_similar(sample: Sample) -> None:
    """Edit one task named among near-identical titles: only it changes (#404).

    Priority is the field because the deployed model sent integers for it, and
    guessed task ids ("Task 291 not found") are the targeting failure.
    """
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Work")
        tasks = {
            title: tasks_service.create_task(db, project_id=ids["Work"], title=title).id
            for title in _Q3_REPORTS
        }
        tasks["Read the Q3 report"] = tasks_service.create_task(
            db, project_id=ids["Home"], title="Read the Q3 report"
        ).id
        target_id = tasks["Review the Q3 report"]
        db.commit()
        last_seed_event = _last_event_id(db)

    result = sample.run('Set "Review the Q3 report" to high priority.')

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "update_task"), "expected a successful update_task"
    with sample.db() as db:
        for title, task_id in tasks.items():
            task = db.get(Task, task_id)
            assert task is not None and task.deleted_at is None and task.title == title
            want = "high" if task_id == target_id else "medium"
            assert task.priority.value == want, f"{title!r}: {task.priority.value}"
            assert task.workflow_status == TaskWorkflowStatus.open
        events = _events_after(db, last_seed_event)
        assert {e.entity_id for e in events} == {target_id}, [
            (e.entity_type, e.entity_id, e.action) for e in events
        ]
        assert all(e.actor == LOOP_ACTOR for e in events)


# --- Multi-turn threads (#389, #405) ---------------------------------------------
#
# Every scenario above is one fresh turn. Chess found that fresh one-turn
# passes hid live failures, so these measure asks inside conversations, through
# ``Sample.converse`` (the conversation route, stored history, a check per
# step). History is text only: the model sees its own earlier *replies*, never
# its earlier tool calls or results (``history_for_loop``). So a reference ("it",
# "that", "the second one") resolves only through what a reply said.


def _assert_completed(thread: Thread) -> None:
    step = thread.step
    assert step.result.stop_reason == "completed", f"stopped: {step.result.stop_reason}"


def _assert_wrote_nothing(thread: Thread) -> None:
    """A read-only or clarifying step: no write landed, none succeeded."""
    step = thread.step
    assert step.events == (), f"wrote {list(step.events)}"
    assert all(r.error is not None for r in _mutations(step.result)), (
        f"mutations: {_trajectory(step.result)}"
    )
    assert thread.before.tasks == step.world.tasks


def _assert_only(thread: Thread, task_id: int, *, actor: str = LOOP_ACTOR) -> None:
    """This step changed ``task_id`` and nothing else, and every row it wrote
    is about that task, written as ``actor``."""
    step = thread.step
    changed = changed_keys(thread.before.tasks, step.world.tasks)
    assert changed == {task_id}, (
        f"changed {sorted(changed)}, want only {task_id}: "
        + str({k: step.world.tasks.get(k) for k in changed})
    )
    assert step.world.projects == thread.before.projects, "a project was touched"
    assert step.events, "the step wrote no activity events"
    assert all(e[0] == "task" and e[1] == task_id for e in step.events), list(step.events)
    assert all(e[3] == actor for e in step.events), list(step.events)


def _new_task(thread: Thread) -> int:
    """The one task this step created."""
    new = thread.step.world.tasks.keys() - thread.before.tasks.keys()
    assert len(new) == 1, f"created {[thread.step.world.tasks[k].title for k in new]}"
    return new.pop()


# The world every long-thread condition shares: what the live-like thread below
# talks about, so nothing it says is false.
def _seed_world(sample: Sample) -> None:
    today = date.today()
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home").id
        work = projects_service.create_project(db, name="Work").id
        errands = projects_service.create_project(db, name="Errands").id
        tasks_service.create_task(db, project_id=home, title="Book dentist appointment")
        tasks_service.create_task(db, project_id=home, title="Rake the leaves")
        tap = tasks_service.create_task(db, project_id=home, title="Fix the leaking kitchen tap")
        tasks_service.mark_done(db, tap)
        tasks_service.create_task(
            db,
            project_id=home,
            title="Order new furnace filters",
            due_date=today + timedelta(days=3),
        )
        tasks_service.create_task(
            db, project_id=work, title="Quarterly report", due_date=today + timedelta(days=5)
        )
        dana = tasks_service.create_task(
            db, project_id=work, title="Email Dana about the offsite"
        )
        tasks_service.mark_done(db, dana)
        tasks_service.create_task(db, project_id=work, title="Prepare standup notes")
        tasks_service.create_task(
            db,
            project_id=errands,
            title="Buy stamps",
            priority=TaskPriority.high,
            due_date=today + timedelta(days=1),
        )
        tasks_service.create_task(db, project_id=errands, title="Pick up dry cleaning")
        db.commit()


def _live_thread(today: date) -> list[tuple[str, str]]:
    """Sixteen exchanges of ordinary use, true of ``_seed_world`` on ``today``.

    Written the way the deployed threads read (short asks, a Glitch-voiced
    reply, an off-topic question, a thank-you). Stored, not generated: the
    condition is the length and shape of the history, and live turns would make
    every sample's history different and cost twenty model runs each.
    """
    report_due = today + timedelta(days=5)
    return [
        (
            "hey, what's on my plate this week?",
            "You've got the quarterly report coming up and furnace filters to order. "
            "The dentist appointment still needs booking too.",
        ),
        (
            "add buy stamps to errands, high priority, due tomorrow",
            'Added "Buy stamps" to Errands: high priority, due tomorrow.',
        ),
        (
            "did I ever fix the kitchen tap?",
            'Yep, "Fix the leaking kitchen tap" is marked done.',
        ),
        (
            "cool. what's in the work project?",
            'Work has three tasks: "Quarterly report" (open), "Email Dana about the '
            'offsite" (open) and "Prepare standup notes" (open).',
        ),
        (
            "mark the email to dana done",
            'Done. "Email Dana about the offsite" is complete.',
        ),
        (
            "how many open tasks do I have in home?",
            'Three open in Home: "Book dentist appointment", "Rake the leaves" and '
            '"Order new furnace filters".',
        ),
        (
            "add pick up dry cleaning to errands",
            'Added "Pick up dry cleaning" to Errands.',
        ),
        (
            "what's the weather like",
            "Can't check the weather, I only run your projects and tasks.",
        ),
        ("fair. anything overdue?", "Nothing's overdue right now."),
        (
            "what does my focus plan look like today",
            "Your plan for today is empty: nothing pinned, no commitments.",
        ),
        (
            "when's the quarterly report due again",
            f"The quarterly report is due {report_due.isoformat()}.",
        ),
        (
            "which project is rake the leaves in?",
            '"Rake the leaves" is in Home.',
        ),
        (
            "anything in errands besides stamps?",
            'Yes, "Pick up dry cleaning".',
        ),
        (
            "how's work looking overall",
            'Work has two open tasks, "Quarterly report" and "Prepare standup notes", '
            "and one done.",
        ),
        (
            "ok I'll deal with the leaves this weekend",
            'Sounds good. "Rake the leaves" stays open until you get to it.',
        ),
        ("thanks glitch", "Anytime."),
    ]


# Each long-thread ask runs under two conditions (chess's ``long_*`` family),
# so the history is the only variable:
#
#   fresh      the same world, an empty conversation       (the control)
#   live_like  the world plus ``_live_thread``'s 16 turns  (what a user has)
#
# A third, ``poisoned`` (live_like plus one last exchange where the agent's OWN
# reply made a false claim about the very thing asked), was red in all three
# families (#405: chess's self-poisoning, reproduced; length alone did
# nothing). It needs #412/#419 rather than a regression fix, so it is measured
# in the frontier tier (``tests/frontier_corpus.py``, ``poisoned_*``, #408).
_CONDITIONS = ("fresh", "live_like")


def _condition_thread(label: str) -> list[tuple[str, str]]:
    return [] if label == "fresh" else _live_thread(date.today())


_REGISTRATION_ASK = 'Add a task "Renew the car registration" to my Home project.'
_HIGH_PRIORITY_ASK = "Now make it high priority."


def _followup_high_priority(sample: Sample, label: str) -> None:
    """Create a task, then "now make it high priority" (#389).

    The second ask names nothing, so it resolves only through the first turn's
    reply in the stored history. The right task is the one step 1 created; it
    alone changes, only its priority moves, and every event is the loop's.
    """
    _seed_world(sample)
    created: list[int] = []

    def check_created(thread: Thread) -> None:
        _assert_completed(thread)
        task_id = _new_task(thread)
        row = thread.step.world.tasks[task_id]
        assert "registration" in row.title.lower(), row.title
        home = next(k for k, p in thread.baseline.projects.items() if p[0] == "Home")
        assert row.project_id == home, f"filed in project {row.project_id}, not Home"
        assert row.parent_task_id is None and not row.deleted
        _assert_only(thread, task_id)
        created.append(task_id)

    def check_high(thread: Thread) -> None:
        _assert_completed(thread)
        task_id = created[0]
        before = thread.before.tasks[task_id]
        after = thread.step.world.tasks[task_id]
        assert after == replace(before, priority="high"), f"{before} -> {after}"
        _assert_only(thread, task_id)

    sample.converse(
        [(_REGISTRATION_ASK, check_created), (_HIGH_PRIORITY_ASK, check_high)],
        thread=_condition_thread(label),
    )


_DENTIST_DONE_ASK = "Mark the dentist appointment task as done."


def _long_complete(sample: Sample, label: str) -> None:
    """``find_and_complete``'s ask inside a thread (#405)."""
    _seed_world(sample)

    def check(thread: Thread) -> None:
        _assert_completed(thread)
        dentist = thread.baseline.task_id("Book dentist appointment")
        assert thread.step.world.tasks[dentist].status == "done", (
            f"still {thread.step.world.tasks[dentist].status}: {thread.step.reply!r}"
        )
        _assert_only(thread, dentist)

    sample.converse([(_DENTIST_DONE_ASK, check)], thread=_condition_thread(label))


def _long_reschedule(sample: Sample, label: str) -> None:
    """``reschedule``'s ask (paraphrased title, ISO target per #380) inside a
    thread (#405)."""
    _seed_world(sample)
    target = date.today() + timedelta(days=10)

    def check(thread: Thread) -> None:
        _assert_completed(thread)
        filters = thread.baseline.task_id("Order new furnace filters")
        before = thread.before.tasks[filters]
        after = thread.step.world.tasks[filters]
        assert after == replace(before, due_date=target), f"{before} -> {after}"
        _assert_only(thread, filters)

    sample.converse(
        [(f"Push the furnace filters task to {target.isoformat()}.", check)],
        thread=_condition_thread(label),
    )


def _seed_home(sample: Sample) -> None:
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home")
        for title in ("Order new furnace filters", "Clean the gutters", "Rake the leaves"):
            tasks_service.create_task(db, project_id=home.id, title=title)
        projects_service.create_project(db, name="Work")
        db.commit()


_NEW_FILTERS_TITLE = "Buy MERV 13 furnace filters"


def _reference_rename_then_undo(sample: Sample) -> None:
    """Find a task; "rename it"; "undo that" (#405).

    The agent has no undo tool, but a rename is undone by putting the old
    title back with ``update_task``, and the old title is in its own earlier
    replies. The honest end state is that: the same task (no re-create, no
    trash) with its original title, nothing else touched. Left renamed is a
    miss whatever the reply says.
    """
    _seed_home(sample)

    def check_found(thread: Thread) -> None:
        _assert_completed(thread)
        _assert_wrote_nothing(thread)
        assert "furnace" in thread.step.reply.lower(), thread.step.reply

    def check_renamed(thread: Thread) -> None:
        _assert_completed(thread)
        filters = thread.baseline.task_id("Order new furnace filters")
        before = thread.before.tasks[filters]
        after = thread.step.world.tasks[filters]
        assert after.title.lower() == _NEW_FILTERS_TITLE.lower(), after.title
        assert after == replace(before, title=after.title), f"{before} -> {after}"
        _assert_only(thread, filters)

    def check_undone(thread: Thread) -> None:
        _assert_completed(thread)
        filters = thread.baseline.task_id("Order new furnace filters")
        original = thread.baseline.tasks[filters]
        after = thread.step.world.tasks[filters]
        assert after.title.lower() == original.title.lower(), (
            f"title is {after.title!r}: {thread.step.reply!r}"
        )
        assert after == replace(original, title=after.title), f"{original} -> {after}"
        _assert_only(thread, filters)
        # And across the whole thread, nothing else moved either.
        assert changed_keys(thread.baseline.tasks, thread.step.world.tasks) <= {filters}

    sample.converse(
        [
            ("Find the task about furnace filters.", check_found),
            (f'Rename it to "{_NEW_FILTERS_TITLE}".', check_renamed),
            ("Undo that.", check_undone),
        ]
    )


def _reference_trash_then_undo(sample: Sample) -> None:
    """Delete a task; "undo that" restores it from the trash (#405).

    The undo of a trash is ``restore_task`` on the same id (the service's
    restore, never a re-create). The end state is the seed exactly.
    """
    _seed_home(sample)

    def check_trashed(thread: Thread) -> None:
        _assert_completed(thread)
        gutters = thread.baseline.task_id("Clean the gutters")
        assert thread.step.world.tasks[gutters].deleted, "expected it in the trash"
        _assert_only(thread, gutters)

    def check_restored(thread: Thread) -> None:
        _assert_completed(thread)
        gutters = thread.baseline.task_id("Clean the gutters")
        assert thread.step.world.tasks == thread.baseline.tasks, (
            f"not back as it was: {thread.step.world.tasks[gutters]}"
        )
        _assert_only(thread, gutters)
        assert ("task", gutters, "restored", LOOP_ACTOR) in thread.step.events

    sample.converse(
        [('Delete the "Clean the gutters" task.', check_trashed), ("Undo that.", check_restored)]
    )


_WORK_CANDIDATES = {
    "Draft the budget": ("Draft the budget", "budget"),
    "Call the printer": ("Call the printer", "printer"),
    "Update the team wiki": ("Update the team wiki", "wiki"),
}
_FRIDAY = 4  # date.weekday()


def _reference_first_to_friday(sample: Sample) -> None:
    """List, then "push the first one to Friday" (#405).

    "First" is the first task the agent's list reply names (``mention_order``,
    the same rule as "the second one"). "Friday" is computed from today's real
    date: the next Friday strictly ahead, or today as well when today is one
    (``weekday_targets``). The model is told only today's ISO date, so the
    weekday arithmetic is its own.
    """
    today = date.today()
    with sample.db() as db:
        work = projects_service.create_project(db, name="Work")
        for title in _WORK_CANDIDATES:
            tasks_service.create_task(db, project_id=work.id, title=title, due_date=today)
        home = projects_service.create_project(db, name="Home")
        tasks_service.create_task(db, project_id=home.id, title="Rake the leaves")
        db.commit()

    def check_listed(thread: Thread) -> None:
        _assert_completed(thread)
        _assert_wrote_nothing(thread)
        named = mention_order(thread.step.reply, _WORK_CANDIDATES)
        assert len(named) >= 2, f"the list names {named}: {thread.step.reply!r}"

    def check_pushed(thread: Thread) -> None:
        _assert_completed(thread)
        first = mention_order(thread.steps[0].reply, _WORK_CANDIDATES)[0]
        target = thread.baseline.task_id(first)
        before = thread.before.tasks[target]
        after = thread.step.world.tasks[target]
        assert after.due_date in weekday_targets(today, _FRIDAY), (
            f"{first!r} due {after.due_date}, want {sorted(weekday_targets(today, _FRIDAY))}"
        )
        assert after == replace(before, due_date=after.due_date), f"{before} -> {after}"
        _assert_only(thread, target)

    sample.converse(
        [
            ("What's open in my Work project?", check_listed),
            ("Push the first one to Friday.", check_pushed),
        ]
    )


def _delegate_thread(sample: Sample) -> None:
    """A conductor-delegated thread: every event of both turns is the
    delegate's, the follow-up included (#405, after ``delegate_actor``)."""
    delegate = "agent:conductor"
    tomorrow = date.today() + timedelta(days=1)
    with sample.db() as db:
        projects_service.create_project(db, name="Home")
        projects_service.create_project(db, name="Work")
        db.commit()
    created: list[int] = []

    def check_created(thread: Thread) -> None:
        _assert_completed(thread)
        assert thread.step.actor == delegate, thread.step.actor
        task_id = _new_task(thread)
        assert "plumber" in thread.step.world.tasks[task_id].title.lower()
        _assert_only(thread, task_id, actor=delegate)
        created.append(task_id)

    def check_due(thread: Thread) -> None:
        _assert_completed(thread)
        assert thread.step.actor == delegate, thread.step.actor
        task_id = created[0]
        before = thread.before.tasks[task_id]
        after = thread.step.world.tasks[task_id]
        assert after == replace(before, due_date=tomorrow), f"{before} -> {after}"
        _assert_only(thread, task_id, actor=delegate)
        # The seed and the conversation's creation stay the user's.
        prior = thread.step.world.events[: len(thread.baseline.events)]
        assert prior == thread.baseline.events and all(e[3] is None for e in prior)

    sample.converse(
        [
            ('Create a task "Call the plumber" in the Home project.', check_created),
            ("Make it due tomorrow.", check_due),
        ],
        headers={"X-Agent-Actor": delegate},
    )


# --- Compositions, date math and misheard speech (#406) -----------------------
#
# Every scenario above asks for one thing, in clean text. These three families
# don't: several writes in one ask (the end state is the whole assertion, with
# "nothing else changed" checked against a snapshot), a date said in words
# (computed from today's real date, never a constant, by the pure helpers in
# ``tests/eval_threads.py``), and a voice transcript with the words mangled
# (the model has to read through it; the app carries no normaliser, by design).


def _world(sample: Sample) -> World:
    with sample.db() as db:
        return World.read(db)


def _assert_task_changes(
    before: World,
    after: World,
    want: Mapping[int, TaskRow],
    *,
    default_project_ok: bool = False,
) -> None:
    """Exactly the tasks in ``want`` changed, each to the row given (a new id
    must be in ``want`` too), and no project was touched. With
    ``default_project_ok`` the service's own "General" may appear: an ask that
    names no project and gets a create with none lands there (the app's
    behavior, not an invention)."""
    changed = changed_keys(before.tasks, after.tasks)
    assert changed == set(want), (
        f"changed {sorted(changed)}, want {sorted(want)}: "
        + str({k: after.tasks.get(k) for k in changed})
    )
    for task_id, row in want.items():
        assert after.tasks[task_id] == row, f"task {task_id}: {after.tasks[task_id]} != {row}"
    projects = {
        pid: row
        for pid, row in after.projects.items()
        if not (default_project_ok and pid not in before.projects and row[0] == "General")
    }
    assert projects == before.projects, "a project was touched"


def _assert_events_only_about(after: World, before: World, task_ids: set[int]) -> None:
    """The run wrote, every row it wrote is the loop's, and none is about a
    task outside ``task_ids`` (a dependency or focus row is not a task row)."""
    events = after.events[len(before.events) :]
    assert events, "the run wrote no activity events"
    assert all(e[3] == LOOP_ACTOR for e in events), list(events)
    stray = {e[1] for e in events if e[0] == "task" and e[1] not in task_ids}
    assert not stray, f"events on other tasks {sorted(stray)}: {list(events)}"


def _new_task_in(before: World, after: World) -> int:
    new = after.tasks.keys() - before.tasks.keys()
    assert len(new) == 1, f"created {[after.tasks[k].title for k in new]}"
    return next(iter(new))


def _compose_create_and_pin(sample: Sample) -> None:
    """"Create X in Garage and pin it to today at 3pm": a create, then a pin on
    the id it returned. End state: one new top-level task in Garage, one Focus
    pin on it at 15:00 today, nothing else."""
    today = date.today()
    with sample.db() as db:
        ids = _seed_projects(db, "Garage", "Home")
        tasks_service.create_task(db, project_id=ids["Garage"], title="Sweep the floor")
        tasks_service.create_task(db, project_id=ids["Home"], title="Rake the leaves")
        garage_id = ids["Garage"]
        db.commit()
    before = _world(sample)

    result = sample.run('Create a task "Sort the screws" in Garage and pin it to today at 3pm.')

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "create_task"), "expected a successful create_task"
    assert _successful(result, "pin_focus_task"), "expected a successful pin_focus_task"
    after = _world(sample)
    task_id = _new_task_in(before, after)
    created = after.tasks[task_id]
    assert "screw" in created.title.lower(), created.title
    _assert_task_changes(
        before,
        after,
        {task_id: replace(created, project_id=garage_id, parent_task_id=None, status="open")},
    )
    with sample.db() as db:
        live = db.execute(
            select(FocusPlanEntry).where(FocusPlanEntry.deleted_at.is_(None))
        ).scalars().all()
        assert [(e.task_id, e.plan_date, e.start_minute) for e in live] == [
            (task_id, today, 15 * 60)
        ], f"focus entries: {[(e.task_id, e.plan_date, e.start_minute) for e in live]}"
    _assert_events_only_about(after, before, {task_id})


def _compose_move_due_today(sample: Sample) -> None:
    """"Move everything due today to tomorrow": every open task due today moves
    to tomorrow, and nothing else does. The judgement calls: *overdue* tasks
    (due earlier) are not "due today" and stay; a task already done stays too,
    still due today (finished work is not rescheduled)."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    with sample.db() as db:
        ids = _seed_projects(db, "Work", "Home")
        work, home = ids["Work"], ids["Home"]
        due_today = {
            tasks_service.create_task(db, project_id=work, title=title, due_date=today).id
            for title in ("Draft the budget", "Call the printer")
        }
        due_today.add(
            tasks_service.create_task(
                db, project_id=home, title="Water the plants", due_date=today
            ).id
        )
        tasks_service.create_task(
            db, project_id=work, title="Update the team wiki", due_date=tomorrow
        )
        tasks_service.create_task(
            db, project_id=home, title="Rake the leaves", due_date=today + timedelta(days=3)
        )
        tasks_service.create_task(
            db, project_id=work, title="Send the invoice", due_date=today - timedelta(days=2)
        )
        tasks_service.create_task(db, project_id=home, title="Fix the leaking tap")
        finished = tasks_service.create_task(
            db, project_id=work, title="Reply to the recruiter", due_date=today
        )
        tasks_service.mark_done(db, finished)
        db.commit()
    before = _world(sample)

    result = sample.run("Move everything due today to tomorrow.")

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "update_task"), "expected a successful update_task"
    after = _world(sample)
    _assert_task_changes(
        before,
        after,
        {task_id: replace(before.tasks[task_id], due_date=tomorrow) for task_id in due_today},
    )
    _assert_events_only_about(after, before, due_today)


def _compose_finish_and_follow_up(sample: Sample) -> None:
    """"Finish the dentist task and add a follow-up for next month": one
    completion, one new open top-level task due in the next calendar month.
    Tolerant rule for "next month": *any* date in that month (first to last
    day), which holds "the same day one month on" and "the 1st" alike. The
    follow-up's title must mention the dentist or say follow-up, and its
    project is not asserted (the ask names none)."""
    first, last = next_month_bounds(date.today())
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Work")
        dentist = tasks_service.create_task(
            db, project_id=ids["Home"], title="Book dentist appointment"
        )
        tasks_service.create_task(db, project_id=ids["Home"], title="Rake the leaves")
        tasks_service.create_task(db, project_id=ids["Work"], title="Draft the budget")
        dentist_id = dentist.id
        db.commit()
    before = _world(sample)

    result = sample.run("Finish the dentist task and add a follow-up for next month.")

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "complete_task"), "expected a successful complete_task"
    assert _successful(result, "create_task"), "expected a successful create_task"
    after = _world(sample)
    follow_up_id = _new_task_in(before, after)
    follow_up = after.tasks[follow_up_id]
    assert follow_up.status == "open" and not follow_up.deleted, follow_up
    assert follow_up.parent_task_id is None, "the follow-up is its own task, not a subtask"
    assert follow_up.due_date is not None and first <= follow_up.due_date <= last, (
        f"follow-up due {follow_up.due_date}, want {first}..{last}"
    )
    assert any(word in follow_up.title.lower() for word in ("dentist", "follow")), follow_up.title
    _assert_task_changes(
        before,
        after,
        {
            dentist_id: replace(before.tasks[dentist_id], status="done"),
            follow_up_id: follow_up,
        },
    )
    _assert_events_only_about(after, before, {dentist_id, follow_up_id})


def _compose_wait_and_bump(sample: Sample) -> None:
    """"Make the report wait on the data pull and bump it to high": one
    dependency edge (the report waits on the data pull) and the report's
    priority high. The data pull stays medium, nothing else changes."""
    with sample.db() as db:
        ids = _seed_projects(db, "Work")
        report = tasks_service.create_task(
            db, project_id=ids["Work"], title="Write the quarterly report"
        )
        data = tasks_service.create_task(db, project_id=ids["Work"], title="Pull the sales data")
        tasks_service.create_task(db, project_id=ids["Work"], title="Water the plants")
        report_id, data_id = report.id, data.id
        db.commit()
    before = _world(sample)

    result = sample.run("Make the report wait on the data pull and bump it to high.")

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "add_dependency"), "expected a successful add_dependency"
    assert _successful(result, "update_task"), "expected a successful update_task"
    after = _world(sample)
    _assert_task_changes(
        before, after, {report_id: replace(before.tasks[report_id], priority="high")}
    )
    with sample.db() as db:
        edges = db.execute(
            select(TaskDependency).where(TaskDependency.deleted_at.is_(None))
        ).scalars().all()
        assert [(e.task_id, e.depends_on_task_id) for e in edges] == [(report_id, data_id)], (
            f"edges (waiter, blocker): {[(e.task_id, e.depends_on_task_id) for e in edges]}"
        )
    _assert_events_only_about(after, before, {report_id})


def _date_ask(sample: Sample, ask: str, accepted: frozenset[date]) -> None:
    """Give the quarterly report (no due date; a bystander has one) a due date
    said in words. The model is told only today's ISO date, so the calendar
    arithmetic is its own; ``accepted`` is every reading of the words we allow."""
    today = date.today()
    with sample.db() as db:
        ids = _seed_projects(db, "Work")
        report = tasks_service.create_task(db, project_id=ids["Work"], title="Quarterly report")
        tasks_service.create_task(
            db,
            project_id=ids["Work"],
            title="Update the team wiki",
            due_date=today + timedelta(days=2),
        )
        report_id = report.id
        db.commit()
    before = _world(sample)

    result = sample.run(ask)

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "update_task"), "expected a successful update_task"
    after = _world(sample)
    due = after.tasks[report_id].due_date
    assert due in accepted, f"due {due}, want one of {sorted(accepted)} (today {today})"
    _assert_task_changes(before, after, {report_id: replace(before.tasks[report_id], due_date=due)})
    _assert_events_only_about(after, before, {report_id})


def _date_next_friday(sample: Sample) -> None:
    """"Next Friday" is ambiguous in English: the coming Friday, or the Friday
    of next week. Both pass (``next_friday_targets``)."""
    _date_ask(
        sample,
        "Make the quarterly report due next Friday.",
        next_friday_targets(date.today()),
    )


def _date_in_two_weeks(sample: Sample) -> None:
    """"In two weeks" is exactly 14 days from today."""
    _date_ask(
        sample,
        "Make the quarterly report due in two weeks.",
        frozenset({date.today() + timedelta(days=14)}),
    )


def _date_end_of_month(sample: Sample) -> None:
    """"The end of the month" is the last day of this month."""
    _date_ask(
        sample,
        "Make the quarterly report due at the end of the month.",
        frozenset({end_of_month(date.today())}),
    )


def _date_this_weekend(sample: Sample) -> None:
    """"This weekend" is a Saturday or Sunday of the current or upcoming
    weekend (``weekend_targets``)."""
    _date_ask(
        sample,
        "Make the quarterly report due this weekend.",
        weekend_targets(date.today()),
    )


def _seed_errands(sample: Sample) -> None:
    """The world the misheard-speech asks refer to: groceries and the dentist
    beside look-alikes ("appointment", "Buy ..."), in two projects."""
    today = date.today()
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Errands")
        home, errands = ids["Home"], ids["Errands"]
        tasks_service.create_task(db, project_id=errands, title="Buy groceries")
        tasks_service.create_task(db, project_id=errands, title="Buy a birthday card")
        tasks_service.create_task(db, project_id=home, title="Dentist appointment", due_date=today)
        tasks_service.create_task(
            db, project_id=home, title="Car service appointment", due_date=today
        )
        tasks_service.create_task(db, project_id=home, title="Rake the leaves")
        db.commit()


def _speech_add_task(sample: Sample) -> None:
    """"ad a task to by stamps": one new open task about stamps, the verb read
    as "buy" (a title still saying "by stamps" is a transcription, not an
    understanding), nothing else changed."""
    _seed_errands(sample)
    before = _world(sample)

    result = sample.run("ad a task to by stamps")

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "create_task"), "expected a successful create_task"
    after = _world(sample)
    task_id = _new_task_in(before, after)
    created = after.tasks[task_id]
    title = created.title.lower()
    assert "stamp" in title, created.title
    assert "by stamps" not in title and "to by" not in title, f"transcribed, not read: {title!r}"
    assert created.status == "open" and created.parent_task_id is None, created
    _assert_task_changes(before, after, {task_id: created}, default_project_ok=True)
    _assert_events_only_about(after, before, {task_id})


def _speech_mark_done(sample: Sample) -> None:
    """"mark the groserys done": "Buy groceries" completed, and not "Buy a
    birthday card" or anything else."""
    _seed_errands(sample)
    before = _world(sample)
    groceries = before.task_id("Buy groceries")

    result = sample.run("mark the groserys done")

    assert result.stop_reason == "completed", f"stopped: {result.stop_reason}"
    assert _successful(result, "complete_task"), "expected a successful complete_task"
    after = _world(sample)
    _assert_task_changes(
        before, after, {groceries: replace(before.tasks[groceries], status="done")}
    )
    _assert_events_only_about(after, before, {groceries})


def _dependency_paraphrase(sample: Sample) -> None:
    """The user describes stored tasks in their own words ("Painting the
    walls" for "Paint the walls", "buying primer" for "Buy primer"): exactly
    one edge, pointing the right way. A tripwire for ``search`` (#444: word
    matching with FTS5 took a direct probe from 5/10 to 10/10; a regression
    to substring matching would find neither title)."""
    _dependency_edge(sample, "Painting the walls is blocked by buying primer.")


# --- The gate: one pytest case per scenario -------------------------------------


def test_eval_create_task_with_fields() -> None:
    _gate("create_task_with_fields", _create_task_with_fields)


def test_eval_find_and_complete() -> None:
    _gate("find_and_complete", _find_and_complete)


def test_eval_reschedule() -> None:
    _gate("reschedule", _reschedule)


def test_eval_delete_is_soft_and_restorable() -> None:
    _gate("delete_is_soft", _delete_is_soft_and_restorable)


def test_eval_read_only_question_mutates_nothing() -> None:
    _gate("read_only_count", _read_only_question_mutates_nothing)


def test_eval_honest_about_missing_task() -> None:
    _gate("honest_about_missing", _honest_about_missing_task)


def test_eval_delegate_actor_attribution() -> None:
    _gate("delegate_actor", _delegate_actor_attribution)


def test_eval_focus_pin_and_commitment() -> None:
    _gate("focus_pin_and_commitment", _focus_pin_and_commitment)


def test_eval_focus_remove_is_soft() -> None:
    _gate("focus_remove_is_soft", _focus_remove_is_soft)


def test_eval_focus_skip_series() -> None:
    _gate("focus_skip_series", _focus_skip_series_not_task)


def test_eval_dependency_add() -> None:
    _gate("dependency_add", _dependency_add)


def test_eval_dependency_remove() -> None:
    _gate("dependency_remove", _dependency_remove)


# A tripwire (#386, #408): 1/20 while ``repeat_interval`` sat inside
# ``changes`` (the model guessed ``recurrence``/``recur_interval`` and then
# created a duplicate task); 20/20 once #411 made it a top-level argument
# (A/B, 2026-10-21). ``long_composition`` in the frontier tier carries it inside
# a harder whole.
def test_eval_recurrence_make_repeating() -> None:
    _gate("recurrence_make_repeating", _recurrence_make_repeating)


def test_eval_recurrence_skip() -> None:
    _gate("recurrence_skip", _recurrence_skip)


def test_eval_recurrence_stop() -> None:
    _gate("recurrence_stop", _recurrence_stop)


def test_eval_restore_task_from_trash() -> None:
    _gate("restore_task", _restore_task_from_trash)


def test_eval_restore_project_from_trash() -> None:
    _gate("restore_project", _restore_project_from_trash)


def test_eval_project_create() -> None:
    _gate("project_create", _project_create)


def test_eval_project_rename() -> None:
    _gate("project_rename", _project_rename)


def test_eval_project_close() -> None:
    _gate("project_close", _project_close)


def test_eval_project_reopen() -> None:
    _gate("project_reopen", _project_reopen)


def test_eval_project_trash() -> None:
    _gate("project_trash", _project_trash)


def test_eval_misfire_batch_create() -> None:
    _gate("misfire_batch_create", _batch_create)


def test_eval_misfire_create_in_named_project() -> None:
    _gate("misfire_create_in_named_project", _create_in_named_project)


# Was the closest of the five to red: 15/20 on gemma-4-12b, 2026-10-20 (13/20
# on main in #411's A/B). The first create sent ``parent_id``; a retry that
# dropped ``project_id`` filed the subtask in an invented General project.
# 20/20 since #411 accepts ``parent_id`` for ``parent_task_id``.
def test_eval_misfire_subtask_under_parent() -> None:
    _gate("misfire_subtask_under_parent", _subtask_under_named_parent)


def test_eval_misfire_complete_parent() -> None:
    _gate("misfire_complete_parent", _complete_parent_with_open_subtasks)


def test_eval_misfire_edit_among_similar() -> None:
    _gate("misfire_edit_among_similar", _edit_among_similar)


# Each family under ``fresh`` and ``live_like`` (#405); ``poisoned`` is
# measured in the frontier tier (``poisoned_*``, #408).
@pytest.mark.parametrize("condition", _CONDITIONS)
def test_eval_followup_high_priority(condition: str) -> None:
    _gate(
        f"followup_high_priority[{condition}]",
        lambda sample: _followup_high_priority(sample, condition),
    )


@pytest.mark.parametrize("condition", _CONDITIONS)
def test_eval_long_complete(condition: str) -> None:
    _gate(f"long_complete[{condition}]", lambda sample: _long_complete(sample, condition))


@pytest.mark.parametrize("condition", _CONDITIONS)
def test_eval_long_reschedule(condition: str) -> None:
    _gate(f"long_reschedule[{condition}]", lambda sample: _long_reschedule(sample, condition))


# ``ambiguous_dentist_then_second`` (0/5, #405) is measured in the frontier
# tier as ``ambiguous_then_pick`` (#408): it needs #420's clarification, not a
# regression fix.


def test_eval_reference_rename_then_undo() -> None:
    _gate("reference_rename_then_undo", _reference_rename_then_undo)


def test_eval_reference_trash_then_undo() -> None:
    _gate("reference_trash_then_undo", _reference_trash_then_undo)


def test_eval_reference_first_to_friday() -> None:
    _gate("reference_first_to_friday", _reference_first_to_friday)


def test_eval_delegate_thread() -> None:
    _gate("delegate_thread", _delegate_thread)


# Compositions, dates and speech (#406).


def test_eval_compose_create_and_pin() -> None:
    _gate("compose_create_and_pin", _compose_create_and_pin)


def test_eval_compose_move_due_today() -> None:
    _gate("compose_move_due_today", _compose_move_due_today)


# A tripwire (#406, #408): 0/20 while the arguments were nested, every sample
# leaving ``due_date`` unset and "next month" in the description; 19/20 once
# #411 made them top-level (A/B, 2026-10-21). ``long_composition`` in the
# frontier tier carries it inside a harder whole.
def test_eval_compose_finish_and_follow_up() -> None:
    _gate("compose_finish_and_follow_up", _compose_finish_and_follow_up)


def test_eval_compose_wait_and_bump() -> None:
    _gate("compose_wait_and_bump", _compose_wait_and_bump)


def test_eval_date_next_friday() -> None:
    _gate("date_next_friday", _date_next_friday)


def test_eval_date_in_two_weeks() -> None:
    _gate("date_in_two_weeks", _date_in_two_weeks)


def test_eval_date_end_of_month() -> None:
    _gate("date_end_of_month", _date_end_of_month)


def test_eval_date_this_weekend() -> None:
    _gate("date_this_weekend", _date_this_weekend)


def test_eval_speech_add_task() -> None:
    _gate("speech_add_task", _speech_add_task)


def test_eval_speech_mark_done() -> None:
    _gate("speech_mark_done", _speech_mark_done)


# ``speech_push_appointment`` (6/20, #406) is measured in the frontier tier as
# ``misheard_push`` (#408): it needs #416/#420's clarification, not a
# regression fix.


def test_eval_dependency_paraphrase() -> None:
    _gate("dependency_paraphrase", _dependency_paraphrase)
