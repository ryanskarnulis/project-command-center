"""Unit tests for the eval gate's reporting (#384, #403); no GPU needed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.eval_report import (
    RunRecord,
    compare,
    format_report,
    int_from_env,
    main,
    schema_errors,
    schema_stats,
    scored_samples,
    summarize,
    unparseable_turns,
    write_jsonl,
)
from tests.evalstats import RateResult


def test_summarize_pass_count_and_ranges() -> None:
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
    assert (beta.runs, beta.passes) == (1, 1)


def test_summarize_tolerates_runs_that_never_reported() -> None:
    (summary,) = summarize([RunRecord("crash", False), RunRecord("crash", True, 3, 1.0)])
    assert (summary.min_iterations, summary.max_iterations) == (3, 3)
    (none_at_all,) = summarize([RunRecord("crash", False)])
    assert none_at_all.min_iterations is None and none_at_all.min_duration is None


def test_format_report_carries_the_verdict_beside_the_ranges() -> None:
    records = [RunRecord("alpha", True, 3, 1.2)] * 4 + [RunRecord("alpha", True, 5, 4.04)]
    records += [RunRecord("beta", i < 1, 2, 0.9) for i in range(5)]
    results = {
        "alpha": RateResult.from_blocks(blocks=[(5, 5)], failures=[], floor=0.8),
        "beta": RateResult.from_blocks(blocks=[(1, 5)], failures=["x"] * 4, floor=0.8, infra=2),
        # Every sample an infra death: a row with a verdict and no ranges.
        "dead": RateResult.from_blocks(blocks=[], failures=[], floor=0.8, infra=5),
    }
    report = format_report(summarize(records), results)
    alpha = next(line for line in report.splitlines() if line.startswith("alpha"))
    assert "5/5" in alpha and "ABOVE_FLOOR" in alpha and "3–5" in alpha and "1.2–4.0" in alpha
    beta = next(line for line in report.splitlines() if line.startswith("beta"))
    assert "1/5" in beta and "BELOW_FLOOR" in beta and " 2 " in beta
    dead = next(line for line in report.splitlines() if line.startswith("dead"))
    assert "0/0" in dead and dead.rstrip().endswith("-")
    assert report.endswith("2/3 scenarios not below their floor")
    assert format_report([], {}) == "[eval-report] no runs recorded"


def test_int_from_env() -> None:
    assert int_from_env("PCC_AGENT_EVALS_RUNS", 5, environ={}) == 5
    assert int_from_env("PCC_AGENT_EVALS_RUNS", 5, environ={"PCC_AGENT_EVALS_RUNS": " "}) == 5
    assert int_from_env("PCC_AGENT_EVALS_RUNS", 5, environ={"PCC_AGENT_EVALS_RUNS": "1"}) == 1
    assert int_from_env("PCC_AGENT_EVALS_SEED", None, minimum=0, environ={}) is None
    assert (
        int_from_env("PCC_AGENT_EVALS_SEED", None, minimum=0, environ={"PCC_AGENT_EVALS_SEED": "0"})
        == 0
    )
    for bad in ("0", "-2", "many"):
        with pytest.raises(ValueError):
            int_from_env("PCC_AGENT_EVALS_RUNS", 5, environ={"PCC_AGENT_EVALS_RUNS": bad})


def _report(path: Path, scenario: str, outcomes: dict[int | None, str]) -> None:
    write_jsonl(path, {"kind": "header", "git_sha": "abc"})
    write_jsonl(
        path,
        {
            "kind": "scenario",
            "scenario": scenario,
            "samples": [{"seed": seed, "outcome": outcome} for seed, outcome in outcomes.items()],
        },
    )
    write_jsonl(path, {"kind": "suite"})


def test_scored_samples_keep_only_seeded_scored_samples(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    # Seed 101's first attempt died (INFRA) and was re-taken on the same seed.
    write_jsonl(
        path,
        {
            "kind": "scenario",
            "scenario": "reschedule",
            "samples": [
                {"seed": 100, "outcome": "PASS"},
                {"seed": 101, "outcome": "INFRA"},
                {"seed": 101, "outcome": "FAIL"},
                {"seed": None, "outcome": "PASS"},
            ],
        },
    )
    lines = path.read_text().splitlines()
    assert all(json.loads(line) for line in lines)
    assert scored_samples(lines) == {"reschedule": {100: True, 101: False}}


def test_compare_pairs_arms_on_shared_seeds() -> None:
    a = {"reschedule": {s: True for s in range(100, 110)}}
    b = {"reschedule": {s: s >= 106 for s in range(100, 115)}}
    (line,) = compare(a, b)
    # Pairs only where both arms scored the seed: 10 shared, A wins 6 of them.
    assert line == "reschedule: A 10/10 · B 9/15 · pairs 10 · A-only 6 · B-only 0 · p=0.0312"
    assert compare({"x": {1: True}}, {}) == ["x: A 1/1 · B 0/0 · no shared seeds"]


def test_main_compares_two_report_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _report(tmp_path / "a.jsonl", "find_and_complete", {1: "PASS", 2: "PASS", 3: "FAIL"})
    _report(tmp_path / "b.jsonl", "find_and_complete", {1: "PASS", 2: "FAIL", 3: "FAIL"})
    assert main([str(tmp_path / "a.jsonl"), str(tmp_path / "b.jsonl")]) == 0
    assert capsys.readouterr().out.strip() == (
        "find_and_complete: A 2/3 · B 1/3 · pairs 3 · A-only 1 · B-only 0 · p=1"
    )
    assert main([]) == 2


_KNOWN = {"create_task", "update_task", "search"}
_PREFIX = "Invalid arguments: "


def test_schema_errors_count_argument_and_unknown_tool_refusals_only() -> None:
    calls = [
        ("create_task", "Invalid arguments: data.title: Field required"),
        ("create_task", None),
        ("update_task", "Task 291 not found"),  # domain refusal: the service answered
        ("make_task", "Unknown tool 'make_task'"),
        ("search", None),
    ]
    assert schema_errors(calls, known_tools=_KNOWN, invalid_prefix=_PREFIX) == 2
    assert schema_errors(calls, known_tools=_KNOWN, invalid_prefix=_PREFIX, unparseable=1) == 3
    assert schema_errors([], known_tools=_KNOWN, invalid_prefix=_PREFIX) == 0


def test_unparseable_turns_are_the_user_messages_after_the_runs_prompt() -> None:
    prompt = "Add a task"
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": prompt},  # history: an earlier, identical ask
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "user", "content": "Your tool call failed before execution: bad JSON."},
        {"role": "assistant", "content": "Added."},
    ]
    assert unparseable_turns(messages, prompt) == 1
    assert unparseable_turns(messages[:4], prompt) == 0
    assert unparseable_turns(messages, "something else") == 0


def test_schema_stats_rate_per_sample_and_per_call() -> None:
    assert schema_stats([(2, 6), (0, 3), (1, 4), (0, 0)]) == {
        "schema_errors": 3,
        "tool_calls": 13,
        "schema_errors_per_sample": 0.75,
        "schema_error_rate": 0.231,
        "samples_with_schema_errors": 2,
    }
    empty = schema_stats([])
    assert empty["schema_errors_per_sample"] is None and empty["schema_error_rate"] is None


def test_format_report_shows_schema_corrections() -> None:
    records = [
        RunRecord("alpha", True, 3, 1.0, tool_calls=4, schema_errors=1),
        RunRecord("alpha", True, 3, 1.0, tool_calls=3, schema_errors=0),
        RunRecord("quiet", True, 1, 0.5),
    ]
    results = {
        name: RateResult.from_blocks(blocks=[(2, 2)], failures=[], floor=0.8)
        for name in ("alpha", "quiet")
    }
    report = format_report(summarize(records), results)
    alpha = next(line for line in report.splitlines() if line.startswith("alpha"))
    assert alpha.endswith("1/7 (0.5/sample)")
    quiet = next(line for line in report.splitlines() if line.startswith("quiet"))
    assert quiet.endswith("-")
