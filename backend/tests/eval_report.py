"""Pure reporting for the agent eval gate (#384, #403).

No model, no DB: ``test_agent_evals.py`` collects one :class:`RunRecord` per
*scored* sample and one :class:`evalstats.RateResult` per scenario, and this
module turns them into the end-of-suite table, the JSONL report's lines, and
the paired comparison of two reports. Unit-tested in normal CI
(``test_eval_report.py``).

Comparing two arms (say ``main`` against a prompt change), each run with the
same ``PCC_AGENT_EVALS_SEED`` and its own ``PCC_AGENT_EVALS_REPORT``::

    cd backend && .venv/bin/python -m tests.eval_report main.jsonl change.jsonl

prints, per scenario, both arms' counts and the exact McNemar test on the
samples the two arms drew with the same seed.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.evalstats import Decision, RateResult, format_paired, paired_counts


@dataclass(frozen=True)
class RunRecord:
    """One scored sample of one scenario. Iterations/duration are ``None`` if
    the run died before the loop reported them."""

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


def int_from_env(
    name: str,
    default: int | None,
    *,
    minimum: int = 1,
    environ: Mapping[str, str] | None = None,
) -> int | None:
    """``name`` as an int of at least ``minimum``; unset or blank is ``default``."""
    raw = (os.environ if environ is None else environ).get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
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


def format_report(
    summaries: list[ScenarioSummary], results: Mapping[str, RateResult]
) -> str:
    """The table printed under ``-s``: count, Wilson interval, verdict, blocks,
    infra retries, iteration and time ranges per scenario.

    Columns mirror the baseline table in ``docs/agent-design.md``. Durations
    include any cold model load, so the first scenario's max can be an outlier.
    A scenario with a result but no scored sample (all infra) still gets a row.
    """
    by_name = {s.scenario: s for s in summaries}
    names = list(dict.fromkeys([*results, *by_name]))
    if not names:
        return "[eval-report] no runs recorded"
    width = max(len("scenario"), *(len(name) for name in names))
    lines = [
        "[eval-report] per-scenario results",
        f"{'scenario'.ljust(width)}  pass    ci           verdict      blocks      "
        "infra  iterations  duration (s)",
    ]
    for name in names:
        result = results.get(name)
        summary = by_name.get(name)
        if result is None:
            count, ci, verdict, blocks, infra = "-", "-", "-", "-", "-"
        else:
            count = f"{result.passed}/{result.runs}"
            ci = f"[{result.interval[0]:.2f},{result.interval[1]:.2f}]"
            verdict = str(result.decision)
            blocks = "+".join(f"{p}/{r}" for p, r in result.blocks) or "none"
            infra = str(result.infra)
        lines.append(
            f"{name.ljust(width)}  {count:<6}  {ci:<11}  {verdict:<11}  {blocks:<10}  "
            f"{infra:<5}  "
            f"{_range(*(_iters(summary)), 'd'):<10}  {_range(*(_durs(summary)), '.1f')}"
        )
    green = sum(r.decision is not Decision.BELOW_FLOOR for r in results.values())
    lines.append(f"{green}/{len(results)} scenarios not below their floor")
    return "\n".join(lines)


def _iters(summary: ScenarioSummary | None) -> tuple[int | None, int | None]:
    return (None, None) if summary is None else (summary.min_iterations, summary.max_iterations)


def _durs(summary: ScenarioSummary | None) -> tuple[float | None, float | None]:
    return (None, None) if summary is None else (summary.min_duration, summary.max_duration)


# --- the JSONL report ---------------------------------------------------------


def write_jsonl(path: str | Path | None, record: Mapping[str, Any]) -> None:
    """Append one line, if a report path was asked for. Failures are printed,
    never raised: losing a diagnostic line must not fail a measured run."""
    if path is None:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:  # pragma: no cover - diagnostics only
        print(f"\n[eval] report write failed: {exc}")


def scored_samples(lines: Iterable[str]) -> dict[str, dict[int, bool]]:
    """Per scenario, each *seeded* scored sample's seed → passed, read from a
    report's ``scenario`` lines. Infra retries and unseeded samples carry no
    pair and are left out."""
    samples: dict[str, dict[int, bool]] = {}
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") != "scenario":
            continue
        seeds = samples.setdefault(record["scenario"], {})
        for sample in record.get("samples", []):
            if sample.get("outcome") in ("PASS", "FAIL") and sample.get("seed") is not None:
                seeds[int(sample["seed"])] = sample["outcome"] == "PASS"
    return samples


def compare(
    a: Mapping[str, Mapping[int, bool]], b: Mapping[str, Mapping[int, bool]]
) -> list[str]:
    """One line per scenario in either arm: both arms' raw counts, then the
    paired verdict on the seeds both arms scored. Read the two together: a
    small p says the arms differ on these seeds, the counts say by how much."""
    lines: list[str] = []
    for scenario in dict.fromkeys([*a, *b]):
        arm_a, arm_b = a.get(scenario, {}), b.get(scenario, {})
        shared = sorted(set(arm_a) & set(arm_b))
        _, a_only, b_only, _ = paired_counts(
            [arm_a[s] for s in shared], [arm_b[s] for s in shared]
        )
        lines.append(
            f"{scenario}: A {sum(arm_a.values())}/{len(arm_a)} · "
            f"B {sum(arm_b.values())}/{len(arm_b)} · "
            + (format_paired(a_only, b_only, len(shared)) if shared else "no shared seeds")
        )
    return lines


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m tests.eval_report A.jsonl B.jsonl", file=sys.stderr)
        return 2
    arms = [scored_samples(Path(path).read_text(encoding="utf-8").splitlines()) for path in argv]
    print("\n".join(compare(arms[0], arms[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
