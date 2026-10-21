"""The campaign report's join logic, off the GPU (#410).

``scripts/campaign_report.py`` turns the per-block JSONL reports of
``eval_campaign.sh`` into one arm table. These fixtures are shaped like the
lines ``test_agent_evals.py`` writes; no model, no server.
"""

from __future__ import annotations

import importlib.util
import sys
import json
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "campaign_report.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("campaign_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cr = _load()


def _scenario(name: str, outcomes: list[str], *, base_seed: int | None = 1000) -> dict[str, Any]:
    scored = [o for o in outcomes if o in ("PASS", "FAIL")]
    passed = scored.count("PASS")
    return {
        "kind": "scenario",
        "scenario": name,
        "passed": passed,
        "runs": len(scored),
        "blocks": [[passed, len(scored)]],
        "samples": [
            {"index": i + 1, "outcome": o, "seed": None if base_seed is None else base_seed + i}
            for i, o in enumerate(outcomes)
        ],
    }


HEADER = {"kind": "header", "git_sha": "abc"}
SUITE = {"kind": "suite", "samples": 10}


def test_aggregate_sums_blocks_per_arm_and_skips_other_kinds() -> None:
    reports = [
        ("a", [HEADER, _scenario("s", ["PASS"] * 5), SUITE]),
        ("b", [HEADER, _scenario("s", ["PASS", "FAIL", "PASS", "PASS", "PASS"]), SUITE]),
        ("a", [_scenario("s", ["PASS", "PASS", "FAIL", "FAIL", "PASS"])]),
        ("b", [_scenario("s", ["FAIL"] * 5)]),
    ]
    table = cr.aggregate(reports)
    assert table["s"]["a"] == {"passed": 8, "runs": 10, "blocks": [5, 3]}
    assert table["s"]["b"] == {"passed": 4, "runs": 10, "blocks": [4, 0]}


def test_aggregate_leaves_out_a_scenario_that_never_got_a_rate() -> None:
    aborted = {"kind": "scenario", "scenario": "s", "decision": "INFRA_ABORTED", "samples": []}
    assert cr.aggregate([("a", [aborted])]) == {}


def test_sample_outcomes_keys_on_block_and_seed_and_drops_unscored_samples() -> None:
    record = _scenario("s", ["PASS", "INFRA", "FAIL", "HARNESS"], base_seed=7)
    out = cr.sample_outcomes([("a", 3, [record])])
    # INFRA and HARNESS were never samples; seeds keep their position.
    assert out == {"s": {"a": {(3, 7): True, (3, 9): False}}}


def test_unseeded_samples_do_not_pair() -> None:
    record = _scenario("s", ["PASS", "FAIL"], base_seed=None)
    assert cr.sample_outcomes([("a", 1, [record])]) == {}


def test_paired_counts_the_discordant_pairs_across_blocks() -> None:
    # Block 1: A passes all five, B fails two. Block 2: both fail the same one.
    files = [
        ("a", 1, [_scenario("s", ["PASS"] * 5, base_seed=1000)]),
        ("b", 1, [_scenario("s", ["PASS", "FAIL", "PASS", "FAIL", "PASS"], base_seed=1000)]),
        ("a", 2, [_scenario("s", ["PASS", "FAIL", "PASS", "PASS", "PASS"], base_seed=2000)]),
        ("b", 2, [_scenario("s", ["PASS", "FAIL", "PASS", "PASS", "PASS"], base_seed=2000)]),
    ]
    verdicts = cr.paired(cr.sample_outcomes(files), ["a", "b"])
    # 10 shared pairs, A-only 2, B-only 0: exact two-sided p = 2 * (1/4) = 0.5.
    assert verdicts == {"s": {"b": "pairs 10 · A-only 2 · B-only 0 · p=0.5"}}


def test_paired_does_not_pair_across_blocks_with_the_same_seed() -> None:
    # The same seed number in different blocks is a different sample.
    files = [
        ("a", 1, [_scenario("s", ["PASS"], base_seed=5)]),
        ("b", 2, [_scenario("s", ["FAIL"], base_seed=5)]),
    ]
    assert cr.paired(cr.sample_outcomes(files), ["a", "b"]) == {}


def test_format_table_has_a_cell_per_arm_and_a_paired_column() -> None:
    table = {
        "s": {
            "a": {"passed": 8, "runs": 10, "blocks": [5, 3]},
            "b": {"passed": 4, "runs": 10, "blocks": [4, 0]},
        },
        "only_a": {"a": {"passed": 5, "runs": 5, "blocks": [5]}},
    }
    verdicts = {"s": {"b": "pairs 10 · A-only 4 · B-only 0 · p=0.125"}}
    lines = cr.format_table(table, ["a", "b"], verdicts).splitlines()
    assert lines[0] == "scenario | a | b | a vs b"
    assert lines[2] == "`only_a` | 5/5 100% (5) | - | -"
    assert lines[3] == (
        "`s` | 8/10 80% (5, 3) | 4/10 40% (4, 0) | pairs 10 · A-only 4 · B-only 0 · p=0.125"
    )


def test_format_table_omits_the_paired_column_when_nothing_paired() -> None:
    table = {"s": {"a": {"passed": 1, "runs": 1, "blocks": [1]}}}
    assert cr.format_table(table, ["a", "b"]).splitlines()[0] == "scenario | a | b"


def test_report_reads_files_and_takes_the_block_from_the_name(tmp_path: Path) -> None:
    for name, outcomes in (
        ("block-1-a.jsonl", ["PASS"] * 5),
        ("block-1-b.jsonl", ["PASS", "FAIL", "FAIL", "PASS", "PASS"]),
    ):
        lines = [HEADER, _scenario("s", outcomes), SUITE]
        (tmp_path / name).write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    text = cr.report([f"a={tmp_path}/block-1-a.jsonl", f"b={tmp_path}/block-1-b.jsonl"])
    assert "`s` | 5/5 100% (5) | 3/5 60% (3) | pairs 5 · A-only 2 · B-only 0 · p=0.5" in text
