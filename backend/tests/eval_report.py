"""Pure aggregation + formatting for the repeated agent evals (#384).

No model, no DB: ``test_agent_evals.py`` collects one :class:`RunRecord` per
scenario run, and this module turns them into per-scenario summaries. Keeping
it pure lets it be unit-tested in normal CI (``test_eval_report.py``) and gives
a statistical gate (#403) one place to read per-run outcomes from.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RunRecord:
    """One run of one scenario. Iterations/duration are ``None`` if the run
    died before the loop reported them (e.g. a provider error raised)."""

    scenario: str
    passed: bool
    iterations: int | None = None
    duration: float | None = None


@dataclass(frozen=True)
class ScenarioSummary:
    scenario: str
    runs: int
    passes: int
    min_iterations: int | None
    max_iterations: int | None
    min_duration: float | None
    max_duration: float | None

    @property
    def passed(self) -> bool:
        """Gate: a scenario passes only if every one of its runs passed."""
        return self.passes == self.runs


def runs_from_env(environ: Mapping[str, str] | None = None) -> int:
    """``PCC_AGENT_EVALS_RUNS`` as a positive int; unset/blank means 1."""
    raw = (os.environ if environ is None else environ).get("PCC_AGENT_EVALS_RUNS")
    if raw is None or not raw.strip():
        return 1
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"PCC_AGENT_EVALS_RUNS must be an integer, got {raw!r}"
        ) from None
    if value < 1:
        raise ValueError(f"PCC_AGENT_EVALS_RUNS must be >= 1, got {value}")
    return value


def summarize(records: list[RunRecord]) -> list[ScenarioSummary]:
    """Group records by scenario (first-seen order) into summaries."""
    grouped: dict[str, list[RunRecord]] = {}
    for record in records:
        grouped.setdefault(record.scenario, []).append(record)
    summaries: list[ScenarioSummary] = []
    for scenario, runs in grouped.items():
        iterations = [r.iterations for r in runs if r.iterations is not None]
        durations = [r.duration for r in runs if r.duration is not None]
        summaries.append(
            ScenarioSummary(
                scenario=scenario,
                runs=len(runs),
                passes=sum(r.passed for r in runs),
                min_iterations=min(iterations) if iterations else None,
                max_iterations=max(iterations) if iterations else None,
                min_duration=min(durations) if durations else None,
                max_duration=max(durations) if durations else None,
            )
        )
    return summaries


def _range(low: float | None, high: float | None, fmt: str) -> str:
    if low is None or high is None:
        return "-"
    lo, hi = format(low, fmt), format(high, fmt)
    return lo if lo == hi else f"{lo}–{hi}"


def format_report(summaries: list[ScenarioSummary]) -> str:
    """The table printed under ``-s``: pass rate, iteration and time ranges.

    Columns mirror the baseline table in ``docs/agent-design.md``. Durations
    include any cold model load, so the first scenario's max can be an outlier.
    """
    if not summaries:
        return "[eval-report] no runs recorded"
    width = max(len(s.scenario) for s in summaries)
    lines = [
        "[eval-report] per-scenario results",
        f"{'scenario'.ljust(width)}  pass      iterations  duration (s)",
    ]
    for s in summaries:
        verdict = "ok  " if s.passed else "FAIL"
        lines.append(
            f"{s.scenario.ljust(width)}  {s.passes}/{s.runs} {verdict}  "
            f"{_range(s.min_iterations, s.max_iterations, 'd'):<10}  "
            f"{_range(s.min_duration, s.max_duration, '.1f')}"
        )
    total_ok = sum(s.passed for s in summaries)
    lines.append(f"{total_ok}/{len(summaries)} scenarios passed all runs")
    return "\n".join(lines)
