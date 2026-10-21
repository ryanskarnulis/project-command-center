"""The frontier history's append and trend, off the GPU (#408).

``scripts/frontier_report.py`` turns a frontier run's JSONL report into
history lines and reads them back as tables. The fixtures are shaped like the
lines ``test_agent_frontier.py`` writes (``FrontierResult.record``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "frontier_report.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("frontier_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fr = _load()


def _header(split: str = "heldout", sha: str = "abc123") -> dict[str, Any]:
    return {
        "kind": "frontier_header",
        "git_sha": sha,
        "git_dirty": False,
        "model": "gemma-4-12b",
        "profile": "gemma-4-12b@1",
        "system_prompt_sha256": "p" * 64,
        "summary_prompt_sha256": "s" * 64,
        "tool_schemas_sha256": "t" * 64,
        "seed": 408,
        "split": split,
        "runs": 5,
    }


def _record(
    name: str,
    passed: int,
    *,
    runs: int = 5,
    split: str = "heldout",
    tier: int = 2,
    fingerprint: str = "f1",
    rubric: float | None = 0.5,
    made: int = 4,
    backed: int = 3,
) -> dict[str, Any]:
    return {
        "kind": "frontier",
        "scenario": name,
        "tier": tier,
        "split": split,
        "fingerprint": fingerprint,
        "runs": runs,
        "passed": passed,
        "interval": [0.0, 1.0],
        "rubric": rubric,
        "checkpoint_hits": {"a": passed, "b": runs},
        "failure_modes": {"missing_write": runs - passed} if runs - passed else {},
        "variants": {"w00": {"passed": min(passed, 1), "runs": 1}},
        "infra": 0,
        "claims": {
            "made": made,
            "backed": backed,
            "families": {"created": {"made": made, "backed": backed}},
            "unbacked": [],
        },
        "samples": [],
    }


def _line(records: list[dict[str, Any]], date: str = "2026-10-21", **header: Any) -> Any:
    lines = fr.summarize([_header(**header), *records], label="x", date=date)
    assert len(lines) == 1
    return lines[0]


def test_summarize_makes_one_line_per_split_with_the_header_config() -> None:
    records = [
        _header("heldout"),
        _record("plan_week", 0, tier=3, rubric=0.4),
        _record("poisoned_complete", 2, tier=1, rubric=0.7123),
        {"kind": "suite"},
        _header("dev"),
        _record("plan_week", 1, split="dev"),
    ]
    lines = fr.summarize(records, label="baseline", date="2026-10-21")
    assert [line["split"] for line in lines] == ["heldout", "dev"]
    heldout = lines[0]
    assert heldout["label"] == "baseline" and heldout["git_sha"] == "abc123"
    assert heldout["model"] == "gemma-4-12b" and heldout["seed"] == 408
    cell = heldout["scenarios"]["poisoned_complete"]
    assert cell["rubric"] == 0.712 and cell["fingerprint"] == "f1"
    assert cell["failure_modes"] == {"missing_write": 3}
    assert cell["variants"] == {"w00": {"passed": 1, "runs": 1}}
    assert heldout["claims"] == {
        "made": 8,
        "backed": 6,
        "families": {"created": {"made": 8, "backed": 6}},
    }


def test_merge_claims_skips_missing_tallies() -> None:
    assert fr.merge_claims([None, {}]) == {"made": 0, "backed": 0, "families": {}}


def test_moved_needs_separated_intervals() -> None:
    assert fr.moved({"passed": 0, "runs": 5}, {"passed": 5, "runs": 5}) == "▲"
    assert fr.moved({"passed": 5, "runs": 5}, {"passed": 0, "runs": 5}) == "▼"
    assert fr.moved({"passed": 2, "runs": 5}, {"passed": 3, "runs": 5}) == ""


def test_a_changed_fingerprint_is_marked_not_compared() -> None:
    before = {"passed": 0, "runs": 5, "fingerprint": "f1"}
    after = {"passed": 5, "runs": 5, "fingerprint": "f2"}
    assert fr.mark(before, after) == fr.CHANGED
    assert fr.mark(None, after) == ""
    assert fr.mark(before, {**after, "fingerprint": "f1"}) == "▲"


def test_trend_prints_marks_totals_and_claims() -> None:
    first = _line([_record("plan_week", 0, tier=3), _record("poisoned_complete", 0, tier=1)])
    second = _line(
        [_record("plan_week", 0, tier=3), _record("poisoned_complete", 5, tier=1)],
        date="2026-10-28",
        sha="def456",
    )
    table = fr.trend([first, second], "heldout")
    rows = table.splitlines()
    assert rows[0].startswith("| tier | scenario | 2026-10-21 abc123 | 2026-10-28 def456 | solved")
    # Tier order, then name.
    assert "`poisoned_complete`" in rows[2] and "5/5 (0.50)▲" in rows[2]
    assert "`plan_week`" in rows[3] and "0/5 (0.50) |" in rows[3]
    assert "| total | 0/10 (0.50) | 5/10 (0.50)▲ |" in table
    assert "| claims backed | 6/8 (75%) | 6/8 (75%) |" in table
    assert fr.trend([first], "dev") == "no dev runs recorded"


def test_totals_are_marked_changed_across_corpora() -> None:
    first = _line([_record("plan_week", 0)])
    second = _line([_record("plan_week", 0), _record("misheard_push", 5)])
    assert fr.total_row([first, second])[1].endswith(fr.CHANGED)
    reworded = _line([_record("plan_week", 0, fingerprint="f2")])
    assert fr.total_row([first, reworded])[1].endswith(fr.CHANGED)
    assert fr.claims_row([first, _line([_record("plan_week", 0, made=0, backed=0)])])[1] == "—"


def test_solved_reads_the_last_two_heldout_runs_on_one_fingerprint() -> None:
    def run(passed: int, fingerprint: str = "f1", split: str = "heldout") -> Any:
        return _line([_record("plan_week", passed, fingerprint=fingerprint, split=split)])

    assert fr.solved([run(4), run(5)]) == ["plan_week"]
    assert fr.solved([run(5)]) == []  # one run is not two
    assert fr.solved([run(5), run(3)]) == []
    assert fr.solved([run(5, "f0"), run(5, "f1")]) == []  # reworded in between
    assert fr.solved([run(5, split="dev"), run(5, split="dev")]) == []  # dev never solves
    assert "| yes |" in fr.trend([run(4), run(5)], "heldout")


def test_wordings_and_modes_read_the_latest_run() -> None:
    history = [_line([_record("plan_week", 0, tier=3), _record("poisoned_complete", 5, tier=1)])]
    table = fr.wordings(history, "heldout")
    assert "| 1 | `poisoned_complete` | `w00` | 1/1 |" in table
    modes = fr.modes(history, "heldout")
    assert "| 3 | `plan_week` | missing_write ×5 |" in modes
    assert "| 1 | `poisoned_complete` | — |" in modes
    assert fr.wordings([], "dev") == "no dev runs recorded"


def test_main_appends_and_prints(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "frontier.jsonl"
    report.write_text(
        "\n".join(json.dumps(r) for r in (_header(), _record("plan_week", 1, tier=3))) + "\n"
    )
    history = tmp_path / "history.jsonl"
    assert fr.main(["--history", str(history), "append", str(report), "--label", "baseline"]) == 0
    assert fr.main(["--history", str(history), "append", str(report), "--label", "again"]) == 0
    lines = [json.loads(line) for line in history.read_text().splitlines()]
    assert [line["label"] for line in lines] == ["baseline", "again"]
    capsys.readouterr()
    assert fr.main(["--history", str(history), "trend", "--variants", "--modes"]) == 0
    out = capsys.readouterr().out
    assert "## heldout" in out and "`plan_week`" in out and "## dev" in out
    assert "the latest run per wording" in out and "failure modes" in out


def test_main_refuses_a_report_without_frontier_records(tmp_path: Path) -> None:
    report = tmp_path / "gate.jsonl"
    report.write_text(json.dumps({"kind": "header"}) + "\n")
    history = tmp_path / "history.jsonl"
    assert fr.main(["--history", str(history), "append", str(report), "--label", "x"]) == 1
    assert not history.exists()
