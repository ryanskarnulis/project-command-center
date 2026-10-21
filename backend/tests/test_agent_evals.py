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
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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
from app.api import routes_agent
from app.db.models import (
    ActivityEvent,
    Base,
    FocusPlanEntry,
    Project,
    Task,
    TaskDependency,
    TaskWorkflowStatus,
)
from app.db.session import enable_sqlite_fk_enforcement, get_db, get_db_write
from app.main import app
from app.services import focus_plan as focus_plan_service
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import task_trash
from app.services import tasks as tasks_service
from app.tools import registry, runtime
from tests.eval_report import RunRecord, format_report, int_from_env, summarize, write_jsonl
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
    }
    write_jsonl(_REPORT_PATH, suite)
    print(f"\n{format_report(summarize(_RECORDS), _RESULTS)}")
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
        result = super().run(
            user_message,
            history=history,
            actor=actor,
            deadline=deadline,
            cancel=cancel,
        )
        self.runs.append((actor, result))
        return result


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

    def provider(self) -> LlamaCppProvider:
        """The configured provider, sending this sample's seed (if any)."""
        return provider_from_settings(seed=self.seed)

    def run(self, prompt: str, *, max_iterations: int = 10) -> AgentRunResult:
        """One run straight through the loop."""
        started = time.monotonic()
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
            with TestClient(app, client=(f"eval-{self.index}", 50000)) as client:
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
        self._print()

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
        if stop_reason == "provider_error":
            self.provider_failure = provider_failure_kind(detail)
        self._print()
        raise _RunDied(detail)

    def _print(self) -> None:
        """The per-sample ``[eval]`` stats line."""
        iterations = None if self.result is None else self.result.iterations
        print(
            f"\n[eval] scenario={self.scenario}[{self.index}] seed={self.seed} "
            f"stop={self.stop_reason} iterations={iterations} "
            f"duration={self.duration or 0.0:.1f}s "
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
            "iterations": None if self.result is None else self.result.iterations,
            "seconds": None if self.duration is None else round(self.duration, 1),
            "trajectory": _trajectory(self.result),
            "run_id": None if self.result is None else self.result.run_id,
            "error": None if error is None else str(error),
            "detail": self.detail,
        }


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
            _RECORDS.append(
                RunRecord(
                    scenario,
                    passed,
                    None if sample.result is None else sample.result.iterations,
                    sample.duration,
                )
            )
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
    print(f"\n[eval] scenario={scenario} PASS_RATE={result.summary()} floor={floor:.0%}")
    for failure in result.failures:
        print(f"[eval]   ✗ {failure}")
    for mode, count in sorted(result.failure_modes.items(), key=lambda item: -item[1]):
        print(f"[eval]   ×{count} {mode}")
    report(**scenario_record(scenario, result, floor))
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
    """Retrieval tripwire: the task is described, not named — look it up, then act."""
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
    # A read (search/list/get) must have located the task before the write.
    tools = [record.tool for record in result.tool_calls]
    first_complete = tools.index("complete_task")
    assert any(_is_read(tool) for tool in tools[:first_complete]), (
        f"no read before complete_task in {tools}"
    )
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

# The reply must concede the miss (#383). Observed temp-1.0 phrasing
# (2026-10-19, 8 runs) is "i can't/couldn't find a task called …"; the rest
# are paraphrases a model could reach for. A success claim ("Done! I marked
# it complete.") matches none of them.
_CONCEDES_MISS_RE = re.compile(
    r"\b(?:"
    r"(?:can'?t|cannot|can not|couldn'?t|could not|didn'?t|did not|don'?t|"
    r"do not|wasn'?t able to|was not able to|unable to|failed to)"
    r"\s+(?:seem to\s+)?(?:find|locate|see|spot)"
    r"|not found|no (?:such |matching )?tasks?|no match(?:es)?"
    r"|doesn'?t exist|does not exist|isn'?t (?:a task|there|in)"
    r")\b",
    re.IGNORECASE,
)


def _concedes_miss(reply: str) -> bool:
    """Whether the reply tells the user the target wasn't found (#383)."""
    return _CONCEDES_MISS_RE.search(reply.replace("\u2019", "'")) is not None


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
    # No writes alone would pass "Done! I marked it complete." (#383).
    assert _concedes_miss(result.reply), (
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

    # Titles are named as stored: paraphrase retrieval ("painting", "buying")
    # is search's problem, not this scenario's (#444).
    result = sample.run('"Paint the walls" is blocked by "Buy primer".')

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


# Known red, kept as a before-number (#386): the model can't find
# ``repeat_interval`` inside ``changes`` (it guesses ``recurrence``,
# ``recur_interval``), the refusal doesn't name the field, and it falls back to
# creating a duplicate task. 1/5 on gemma-4-12b, 2026-10-20. #411 flattens the
# arguments and #413 makes refusals say how to recover; drop the mark when it
# goes green. Until the frontier tier (#408), xfail runs it without gating.
@pytest.mark.xfail(
    strict=False, reason="below floor until #411/#413: repeat_interval is hard to find"
)
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
