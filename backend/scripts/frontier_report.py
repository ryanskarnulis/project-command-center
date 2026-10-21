#!/usr/bin/env python3
"""The frontier tier's history: record a run, and read the trend (#408).

    # after a run with PCC_AGENT_EVALS_REPORT=/tmp/frontier.jsonl
    python scripts/frontier_report.py append /tmp/frontier.jsonl --label "after #411"
    python scripts/frontier_report.py trend [--split heldout] [--last 6] [--variants] [--modes]

`append` turns one run's report (a `frontier_header` and a `frontier` record
per scenario, `tests/test_agent_frontier.py`) into one summary line per split
in `docs/frontier-history.jsonl`: the configuration (git sha, model, profile,
prompt and tool-schema hashes), and per scenario its fingerprint, passes,
runs, rubric, checkpoint hits, failure modes and passes per wording, plus the
split's claim accuracy (`tests/claims.py`). `trend` prints, per split, a
scenario × run table with a mark where a run moved against the one before it,
a corpus total row and a claim-accuracy row under it, and marks solved
scenarios; `--variants` adds the latest run wording by wording, `--modes` its
failure modes.

Three readings keep the table honest (`docs/agent-frontier.md`):

- **A mark means the intervals separated, nothing less.** ▲/▼ appears only
  when the one-sided 95% Wilson intervals of two consecutive runs do not
  overlap. At five samples a scenario needs a large move to earn one, which
  is the point: runs on different days sit on differently-warmed servers, and
  consecutive samples of one prompt are correlated, so the history shows
  *trend*. A claim that a change moved a scenario is an A/B on one server
  (`docs/agent-campaigns.md`).
- **A changed scenario is never compared with its old self.** Each cell
  carries its scenario's fingerprint for the split (the wordings, the
  checkpoints, the stored history's name and the revision); a cell whose
  fingerprint differs from the previous one is marked `†` instead of
  compared. The total and claims rows are marked only between two runs of
  the same corpus: the same scenarios on the same fingerprints.
- **Solved reads held-out only, and only informs.** A scenario is solved
  when its held-out whole-task rate is at least 0.8 in each of the two most
  recent held-out runs, both on one fingerprint. A solved scenario stays in
  the corpus.

Pure functions over dicts, tested off the GPU in `tests/test_frontier_report.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.evalstats import wilson_interval  # noqa: E402

HISTORY = Path(__file__).resolve().parents[2] / "docs" / "frontier-history.jsonl"
SOLVED_RATE = 0.8
SOLVED_RUNS = 2
# The mark of a cell that measured something other than the cell before it:
# a reworded or regraded scenario, or a run over a different corpus.
CHANGED = "†"
_CONFIG_KEYS = (
    "git_sha",
    "git_dirty",
    "model",
    "profile",
    "system_prompt_sha256",
    "summary_prompt_sha256",
    "tool_schemas_sha256",
    "seed",
)


def merge_claims(tallies: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    """Several scenarios' claim tallies (`Tally.as_dict`) as one: made and
    backed in all and per family. The unbacked lines stay in the report."""
    made = backed = 0
    families: dict[str, Counter[str]] = {}
    for tally in tallies:
        if not tally:
            continue
        made += tally.get("made", 0)
        backed += tally.get("backed", 0)
        for family, counts in (tally.get("families") or {}).items():
            cell = families.setdefault(family, Counter())
            cell.update({"made": counts["made"], "backed": counts["backed"]})
    return {
        "made": made,
        "backed": backed,
        "families": {name: dict(cell) for name, cell in sorted(families.items())},
    }


def summarize(records: Iterable[dict[str, Any]], *, label: str, date: str) -> list[dict[str, Any]]:
    """One history line per split present in a run's report records.

    A report may hold several headers (a run per split appended to one file);
    each `frontier` record carries its own split, and the configuration comes
    from the header that opened it.
    """
    lines: dict[str, dict[str, Any]] = {}
    claims: dict[str, list[dict[str, Any] | None]] = {}
    header: dict[str, Any] = {}
    for record in records:
        if record.get("kind") == "frontier_header":
            header = record
            continue
        if record.get("kind") != "frontier":
            continue
        split = record["split"]
        line = lines.setdefault(
            split,
            {
                "date": date,
                "label": label,
                **{key: header.get(key) for key in _CONFIG_KEYS},
                "split": split,
                "runs": header.get("runs"),
                "scenarios": {},
            },
        )
        line["scenarios"][record["scenario"]] = {
            "tier": record["tier"],
            "fingerprint": record.get("fingerprint"),
            "passed": record["passed"],
            "runs": record["runs"],
            "rubric": None if record["rubric"] is None else round(record["rubric"], 3),
            "infra": record["infra"],
            "checkpoint_hits": record["checkpoint_hits"],
            "failure_modes": record.get("failure_modes") or {},
            "variants": record.get("variants") or {},
        }
        claims.setdefault(split, []).append(record.get("claims"))
    for split, line in lines.items():
        line["claims"] = merge_claims(claims[split])
    return list(lines.values())


def _rate(cell: dict[str, Any]) -> float:
    return cell["passed"] / cell["runs"] if cell["runs"] else 0.0


def moved(before: dict[str, Any], after: dict[str, Any]) -> str:
    """▲ or ▼ when the two runs' Wilson intervals do not overlap, else ''."""
    low_b, high_b = wilson_interval(before["passed"], before["runs"])
    low_a, high_a = wilson_interval(after["passed"], after["runs"])
    if low_a > high_b:
        return "▲"
    if high_a < low_b:
        return "▼"
    return ""


def mark(previous: dict[str, Any] | None, cell: dict[str, Any]) -> str:
    """A scenario cell's mark against the one before it: `CHANGED` when the
    scenario's wordings or checkpoints changed in between, since the two
    numbers are of different things; else `moved`'s."""
    if previous is None:
        return ""
    if previous.get("fingerprint") != cell.get("fingerprint"):
        return CHANGED
    return moved(previous, cell)


def corpus(line: dict[str, Any]) -> dict[str, str | None]:
    """What a run measured, scenario by scenario. A total or a claims rate is
    comparable between two runs only when this is the same for both."""
    return {name: cell.get("fingerprint") for name, cell in line["scenarios"].items()}


def _marked_row(
    runs: Sequence[dict[str, Any]],
    read: Callable[[dict[str, Any]], dict[str, Any] | None],
    show: Callable[[dict[str, Any]], str],
) -> list[str]:
    """One cell per run for a whole-run row. `read` gives the run's
    `{"passed", "runs"}` (None prints `—`), `show` prints it, and the mark is
    against the last run that had one: `CHANGED` when the two measured
    different corpora, else `moved`'s."""
    cells = []
    previous: tuple[dict[str, Any], dict[str, Any]] | None = None
    for line in runs:
        cell = read(line)
        if cell is None:
            cells.append("—")
            continue
        if previous is None:
            tag = ""
        elif corpus(previous[0]) != corpus(line):
            tag = CHANGED
        else:
            tag = moved(previous[1], cell)
        cells.append(show(cell) + tag)
        previous = (line, cell)
    return cells


def _total_cell(line: dict[str, Any]) -> dict[str, Any] | None:
    """A run's whole-task passes over every scenario it measured, and the
    scenarios' mean rubric."""
    cells = list(line["scenarios"].values())
    if not cells:
        return None
    rubrics = [c["rubric"] for c in cells if c["rubric"] is not None]
    return {
        "passed": sum(c["passed"] for c in cells),
        "runs": sum(c["runs"] for c in cells),
        "rubric": sum(rubrics) / len(rubrics) if rubrics else None,
    }


def total_row(runs: Sequence[dict[str, Any]]) -> list[str]:
    """One cell per run: the corpus total, `passed/runs (mean rubric)`. The
    headline number at one sample per wording, where a scenario's own five
    rarely separate from anything."""

    def show(cell: dict[str, Any]) -> str:
        rubric = "—" if cell["rubric"] is None else f"{cell['rubric']:.2f}"
        return f"{cell['passed']}/{cell['runs']} ({rubric})"

    return _marked_row(runs, _total_cell, show)


def _claims_cell(line: dict[str, Any]) -> dict[str, Any] | None:
    claims = line.get("claims") or {}
    if not claims.get("made"):
        return None
    return {"passed": claims["backed"], "runs": claims["made"]}


def claims_row(runs: Sequence[dict[str, Any]]) -> list[str]:
    """One cell per run: claims `backed/made (pct)`, marked like the total."""

    def show(cell: dict[str, Any]) -> str:
        return f"{cell['passed']}/{cell['runs']} ({_rate(cell):.0%})"

    return _marked_row(runs, _claims_cell, show)


def solved(history: Sequence[dict[str, Any]]) -> list[str]:
    """Scenarios at or above `SOLVED_RATE` in each of the last `SOLVED_RUNS`
    held-out runs that measured them, all on one fingerprint. A mark, not a
    verdict: a solved scenario stays in the corpus."""
    heldout = [line for line in history if line["split"] == "heldout"]
    names = {name for line in heldout for name in line["scenarios"]}
    done = []
    for name in sorted(names):
        cells = [line["scenarios"][name] for line in heldout if name in line["scenarios"]]
        recent = cells[-SOLVED_RUNS:]
        if (
            len(recent) == SOLVED_RUNS
            and len({c.get("fingerprint") for c in recent}) == 1
            and all(c["runs"] and _rate(c) >= SOLVED_RATE for c in recent)
        ):
            done.append(name)
    return done


def _names(runs: Sequence[dict[str, Any]]) -> list[str]:
    """Every scenario the runs measured, by tier then name."""
    tiers: dict[str, int] = {}
    for line in runs:
        for name, cell in line["scenarios"].items():
            tiers.setdefault(name, cell["tier"])
    return sorted(tiers, key=lambda n: (tiers[n], n))


def trend(history: Sequence[dict[str, Any]], split: str, last: int = 6) -> str:
    """The split's table: one column per run (oldest first), `passed/runs
    (rubric)` per cell and its mark against the previous run, the corpus
    total and the claims row under it, and a solved column (held-out only)."""
    runs = [line for line in history if line["split"] == split][-last:]
    if not runs:
        return f"no {split} runs recorded"
    done = set(solved(history)) if split == "heldout" else set()
    head = ["tier", "scenario"] + [
        f"{line['date']} {(line.get('git_sha') or '')[:8]}".strip() for line in runs
    ]
    if split == "heldout":
        head.append("solved")
    rows = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    for name in _names(runs):
        tier = next(r["scenarios"][name]["tier"] for r in runs if name in r["scenarios"])
        cells = []
        previous = None
        for line in runs:
            cell = line["scenarios"].get(name)
            if cell is None:
                cells.append("—")
                continue
            rubric = "—" if cell["rubric"] is None else f"{cell['rubric']:.2f}"
            cells.append(f"{cell['passed']}/{cell['runs']} ({rubric}){mark(previous, cell)}")
            previous = cell
        row = [str(tier), f"`{name}`", *cells]
        if split == "heldout":
            row.append("yes" if name in done else "")
        rows.append("| " + " | ".join(row) + " |")
    for label, cells in (("total", total_row(runs)), ("claims backed", claims_row(runs))):
        row = ["", label, *cells]
        if split == "heldout":
            row.append("")
        rows.append("| " + " | ".join(row) + " |")
    return "\n".join(rows)


def _latest(history: Sequence[dict[str, Any]], split: str) -> dict[str, Any] | None:
    runs = [line for line in history if line["split"] == split]
    return runs[-1] if runs else None


def wordings(history: Sequence[dict[str, Any]], split: str) -> str:
    """The split's latest run, wording by wording: which phrasings each
    scenario's number came from."""
    line = _latest(history, split)
    if line is None:
        return f"no {split} runs recorded"
    scenarios = line["scenarios"]
    rows = ["| tier | scenario | wording | passed |", "| --- | --- | --- | --- |"]
    for name in _names([line]):
        cell = scenarios[name]
        for wording, counts in (cell.get("variants") or {}).items():
            rows.append(
                f"| {cell['tier']} | `{name}` | `{wording}` | {counts['passed']}/{counts['runs']} |"
            )
    if len(rows) == 2:
        return f"the latest {split} run has no per-wording counts"
    return "\n".join(rows)


def modes(history: Sequence[dict[str, Any]], split: str) -> str:
    """The split's latest run: each scenario's failure modes, most first."""
    line = _latest(history, split)
    if line is None:
        return f"no {split} runs recorded"
    rows = ["| tier | scenario | failure modes |", "| --- | --- | --- |"]
    for name in _names([line]):
        cell = line["scenarios"][name]
        counted = sorted((cell.get("failure_modes") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{mode} ×{count}" for mode, count in counted) or "—"
        rows.append(f"| {cell['tier']} | `{name}` | {shown} |")
    return "\n".join(rows)


def _read(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--history", type=Path, default=HISTORY)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("append", help="record a run's report in the history")
    add.add_argument("report", type=Path)
    add.add_argument("--label", required=True)
    add.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    show = sub.add_parser("trend", help="print the history as tables")
    show.add_argument("--split", choices=["dev", "heldout"], action="append")
    show.add_argument("--last", type=int, default=6)
    show.add_argument("--variants", action="store_true", help="add the latest run per wording")
    show.add_argument("--modes", action="store_true", help="add the latest run's failure modes")
    args = parser.parse_args(argv)

    if args.command == "append":
        lines = summarize(_read(args.report), label=args.label, date=args.date)
        if not lines:
            print(f"no frontier records in {args.report}", file=sys.stderr)
            return 1
        with args.history.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")
        print(f"appended {len(lines)} line(s) to {args.history}")
        return 0

    history = _read(args.history) if args.history.exists() else []
    # Held-out first: it is the number of record.
    for split in args.split or ["heldout", "dev"]:
        print(f"\n## {split}\n")
        print(trend(history, split, args.last))
        if args.variants:
            print(f"\n### {split}, the latest run per wording\n")
            print(wordings(history, split))
        if args.modes:
            print(f"\n### {split}, the latest run's failure modes\n")
            print(modes(history, split))
    return 0


if __name__ == "__main__":
    sys.exit(main())
