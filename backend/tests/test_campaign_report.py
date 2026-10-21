"""The campaign report's join logic, off the GPU (#410).

``scripts/campaign_report.py`` turns the per-block JSONL reports of
``eval_campaign.sh`` into one arm table. These fixtures are shaped like the
lines ``test_agent_evals.py`` writes; no model, no server.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

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


# --- the frontier tier (#460) -----------------------------------------------------


def _frontier(
    name: str,
    wholes: list[bool],
    *,
    scores: list[float] | None = None,
    base_seed: int | None = 1000,
    fingerprint: str = "fp1",
) -> dict[str, Any]:
    scores = scores if scores is not None else [1.0 if w else 0.0 for w in wholes]
    return {
        "kind": "frontier",
        "scenario": name,
        "split": "dev",
        "fingerprint": fingerprint,
        "runs": len(wholes),
        "passed": sum(wholes),
        "rubric": sum(scores) / len(scores),
        "samples": [
            {
                "variant": f"w{i}",
                "seed": None if base_seed is None else base_seed + i,
                "whole": w,
                "score": s,
            }
            for i, (w, s) in enumerate(zip(wholes, scores, strict=True))
        ],
    }


FHEADER = {"kind": "frontier_header", "split": "dev"}


def test_frontier_aggregate_sums_passes_and_rubric_per_arm() -> None:
    reports = [
        ("a", [FHEADER, _frontier("s", [True, False], scores=[1.0, 0.5])]),
        ("a", [_frontier("s", [False, False], scores=[0.5, 0.0])]),
        ("b", [_frontier("s", [True, True])]),
        ("a", [_scenario("gate_only", ["PASS"])]),
    ]
    table = cr.frontier_aggregate(reports)
    assert table == {
        "s": {
            "a": {"passed": 1, "runs": 4, "blocks": [1, 0], "score": 2.0},
            "b": {"passed": 2, "runs": 2, "blocks": [2], "score": 2.0},
        }
    }


def test_frontier_outcomes_pair_on_whole_by_block_and_seed() -> None:
    files = [
        ("a", 1, [_frontier("s", [True, True, True, False], base_seed=1000)]),
        ("b", 1, [_frontier("s", [True, False, False, False], base_seed=1000)]),
        ("a", 2, [_frontier("s", [True], base_seed=2000)]),
        ("b", 2, [_frontier("s", [False], base_seed=2000)]),
    ]
    verdicts = cr.paired(cr.frontier_outcomes(files), ["a", "b"])
    # 5 pairs; A-only: block 1 samples 1 and 2, block 2 sample 0; B-only none.
    assert verdicts == {"s": {"b": "pairs 5 · A-only 3 · B-only 0 · p=0.25"}}


def test_unseeded_frontier_samples_do_not_pair() -> None:
    record = _frontier("s", [True, False], base_seed=None)
    assert cr.frontier_outcomes([("a", 1, [record])]) == {}


def test_differing_fingerprints_are_refused() -> None:
    files = [
        ("a", 1, [_frontier("s", [True], fingerprint="fp1")]),
        ("b", 1, [_frontier("s", [True], fingerprint="fp2")]),
    ]
    with pytest.raises(SystemExit, match=r"scenario 's' fingerprint differs.*fp1.*fp2"):
        cr.check_fingerprints(files)


def test_differing_splits_are_refused() -> None:
    files = [
        ("a", 1, [{"kind": "frontier_header", "split": "dev"}]),
        ("b", 1, [{"kind": "frontier_header", "split": "heldout"}]),
    ]
    with pytest.raises(SystemExit, match="split differs"):
        cr.check_fingerprints(files)


def test_same_fingerprints_pass_the_check() -> None:
    files = [
        ("a", 1, [FHEADER, _frontier("s", [True])]),
        ("b", 2, [FHEADER, _frontier("s", [False])]),
    ]
    cr.check_fingerprints(files)


def test_frontier_table_shows_rubric_and_paired_column() -> None:
    reports = [
        ("a", [_frontier("s", [True, True, False, False], scores=[1.0, 1.0, 0.5, 0.0])]),
        ("b", [_frontier("s", [True, False, False, False], scores=[1.0, 0.5, 0.0, 0.0])]),
    ]
    files = [(arm, 1, records) for arm, records in reports]
    text = cr.format_frontier_table(
        cr.frontier_aggregate(reports),
        ["a", "b"],
        cr.paired(cr.frontier_outcomes(files), ["a", "b"]),
    )
    assert text.splitlines()[0] == "scenario | a | b | a vs b"
    assert text.splitlines()[2] == (
        "`s` | 2/4 50% rubric 0.62 (2) | 1/4 25% rubric 0.38 (1) | "
        "pairs 4 · A-only 1 · B-only 0 · p=1"
    )


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_report_joins_frontier_files_and_leaves_the_gate_table_out(tmp_path: Path) -> None:
    _write(tmp_path / "block-1-a.jsonl", [FHEADER, _frontier("s", [True, True])])
    _write(tmp_path / "block-1-b.jsonl", [FHEADER, _frontier("s", [True, False])])
    text = cr.report([f"a={tmp_path}/block-1-a.jsonl", f"b={tmp_path}/block-1-b.jsonl"])
    assert text.startswith("frontier\n")
    assert "`s` | 2/2 100% rubric 1.00 (2) | 1/2 50% rubric 0.50 (1) | pairs 2 · A-only 1" in text


def test_report_refuses_mismatched_fingerprints_end_to_end(tmp_path: Path) -> None:
    _write(tmp_path / "block-1-a.jsonl", [_frontier("s", [True], fingerprint="x")])
    _write(tmp_path / "block-2-a.jsonl", [_frontier("s", [True], fingerprint="y")])
    with pytest.raises(SystemExit, match="refusing to join"):
        cr.report([f"a={tmp_path}/block-1-a.jsonl", f"a={tmp_path}/block-2-a.jsonl"])


def test_report_prints_gate_and_frontier_tables_when_both_are_present(tmp_path: Path) -> None:
    _write(tmp_path / "block-1-a.jsonl", [_scenario("g", ["PASS"]), _frontier("s", [True])])
    text = cr.report([f"a={tmp_path}/block-1-a.jsonl"])
    assert "`g` |" in text and "\nfrontier\n" in text
