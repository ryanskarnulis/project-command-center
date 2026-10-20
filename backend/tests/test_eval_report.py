"""Unit tests for the eval repeat-count aggregation (#384); no GPU needed."""

from __future__ import annotations

import pytest

from tests.eval_report import (
    RunRecord,
    format_report,
    runs_from_env,
    summarize,
)


def test_summarize_pass_rate_and_ranges() -> None:
    records = [
        RunRecord("alpha", True, 3, 1.2),
        RunRecord("beta", True, 2, 0.9),
        RunRecord("alpha", False, 5, 4.0),
        RunRecord("alpha", True, 4, 2.0),
    ]
    alpha, beta = summarize(records)
    assert (alpha.scenario, alpha.runs, alpha.passes) == ("alpha", 3, 2)
    assert (alpha.min_iterations, alpha.max_iterations) == (3, 5)
    assert (alpha.min_duration, alpha.max_duration) == (1.2, 4.0)
    assert not alpha.passed  # one failing run fails the scenario
    assert beta.passed and beta.runs == 1


def test_summarize_tolerates_runs_that_never_reported() -> None:
    (summary,) = summarize(
        [RunRecord("crash", False), RunRecord("crash", True, 3, 1.0)]
    )
    assert (summary.min_iterations, summary.max_iterations) == (3, 3)
    (none_at_all,) = summarize([RunRecord("crash", False)])
    assert none_at_all.min_iterations is None and none_at_all.min_duration is None


def test_format_report() -> None:
    report = format_report(
        summarize(
            [
                RunRecord("alpha", True, 3, 1.2),
                RunRecord("alpha", True, 5, 4.04),
                RunRecord("beta", False, 2, 0.9),
                RunRecord("beta", True, 2, 0.9),
                RunRecord("dead", False),
            ]
        )
    )
    assert "alpha  2/2 ok    3–5" in report
    assert "1.2–4.0" in report
    assert "beta   1/2 FAIL  2 " in report
    assert "dead   0/1 FAIL  -" in report
    assert report.endswith("1/3 scenarios passed all runs")
    assert format_report([]) == "[eval-report] no runs recorded"


def test_runs_from_env() -> None:
    assert runs_from_env({}) == 1
    assert runs_from_env({"PCC_AGENT_EVALS_RUNS": " "}) == 1
    assert runs_from_env({"PCC_AGENT_EVALS_RUNS": "5"}) == 5
    for bad in ("0", "-2", "many"):
        with pytest.raises(ValueError):
            runs_from_env({"PCC_AGENT_EVALS_RUNS": bad})
