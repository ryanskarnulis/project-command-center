#!/usr/bin/env python3
"""Join the eval harness's per-block JSONL reports into one arm table (#410).

`eval_campaign.sh` runs the gate in alternating blocks between two trees on
one server and writes one `PCC_AGENT_EVALS_REPORT` file per block, named
`block-<n>-<arm>.jsonl`. This joins them: per scenario, per arm, the passes
over the runs, the rate, and each block's passes, so the record reads
"17/20 85% (5, 3, 4, 5)".

    python scripts/campaign_report.py a=out/block-1-a.jsonl b=out/block-1-b.jsonl ...

**Paired arms.** A seeded campaign runs sample *j* of block *n* with the same
sampling seed in both arms, and each sample record carries its seed. Samples
of two arms that share (block, seed) are a pair, and each arm after the first
gets a column against the first: the discordant pairs and the exact McNemar p
(`evalstats.mcnemar_exact`). For a small change most pairs agree, so the paired
p resolves what the raw counts cannot. Only scored samples (`PASS`/`FAIL`)
pair; an infra retry or a harness error was never a sample. Unseeded reports
print no paired column. The block number comes from the report's file name.

Pure aggregation over the harness's `scenario` lines (`evalstats.scenario_record`
plus the per-sample records `test_agent_evals.py` appends); header and suite
lines are skipped. Tested off the GPU in `tests/test_campaign_report.py`.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.evalstats import format_paired, paired_counts  # noqa: E402

# The gate's scored outcomes (`evalstats.Outcome`); anything else was not a
# sample — a re-taken infra death, a harness bug — and pairs with nothing.
_SCORED = {"PASS": True, "FAIL": False}
_BLOCK = re.compile(r"block-(\d+)-")

Cell = dict[str, Any]
Table = dict[str, dict[str, Cell]]
Outcomes = dict[str, dict[str, dict[tuple[int | None, int], bool]]]


def aggregate(reports: Iterable[tuple[str, Iterable[dict[str, Any]]]]) -> Table:
    """`reports` is (arm, records) per block file, in the order the blocks ran.
    Returns {scenario: {arm: {"passed", "runs", "blocks"}}}, where "blocks" is
    each sampling block's passes as the harness scored it. A scenario that
    never got a rate (INFRA_ABORTED, PROVIDER_REJECTED, a harness error) has no
    `passed`/`runs` and is left out: no sample, no row."""
    table: Table = {}
    for arm, records in reports:
        for record in records:
            if record.get("kind") != "scenario" or "runs" not in record:
                continue
            cell = table.setdefault(record["scenario"], {}).setdefault(
                arm, {"passed": 0, "runs": 0, "blocks": []}
            )
            cell["passed"] += int(record["passed"])
            cell["runs"] += int(record["runs"])
            blocks = record.get("blocks") or [[record["passed"], record["runs"]]]
            cell["blocks"].extend(int(block[0]) for block in blocks)
    return table


def sample_outcomes(
    reports: Iterable[tuple[str, int | None, Iterable[dict[str, Any]]]],
) -> Outcomes:
    """`reports` is (arm, block, records) per block file. Returns
    {scenario: {arm: {(block, seed): passed}}} over the seeded, scored samples."""
    outcomes: Outcomes = {}
    for arm, block, records in reports:
        for record in records:
            if record.get("kind") != "scenario":
                continue
            for sample in record.get("samples") or []:
                seed = sample.get("seed")
                passed = _SCORED.get(str(sample.get("outcome")))
                if seed is None or passed is None:
                    continue
                outcomes.setdefault(record["scenario"], {}).setdefault(arm, {})[
                    (block, int(seed))
                ] = passed
    return outcomes


def paired(outcomes: Outcomes, arms: Sequence[str]) -> dict[str, dict[str, str]]:
    """{scenario: {arm: verdict}} for each arm after the first, against it,
    over the samples both ran with the same seed in the same block."""
    verdicts: dict[str, dict[str, str]] = {}
    for scenario, by_arm in outcomes.items():
        base = by_arm.get(arms[0], {})
        for arm in arms[1:]:
            other = by_arm.get(arm, {})
            keys = sorted(base.keys() & other.keys(), key=lambda k: (k[0] or 0, k[1]))
            if not keys:
                continue
            _, a_only, b_only, _ = paired_counts([base[k] for k in keys], [other[k] for k in keys])
            verdicts.setdefault(scenario, {})[arm] = format_paired(a_only, b_only, len(keys))
    return verdicts


def format_table(
    table: Table,
    arms: Sequence[str],
    verdicts: dict[str, dict[str, str]] | None = None,
) -> str:
    """A Markdown table, one row per scenario, one column per arm, and a paired
    column per later arm (only when some scenario paired)."""
    verdicts = verdicts or {}
    against = [arm for arm in arms[1:] if any(arm in row for row in verdicts.values())]
    headers = [*arms, *(f"{arms[0]} vs {arm}" for arm in against)]
    lines = [
        "scenario | " + " | ".join(headers),
        "--- | " + " | ".join("---" for _ in headers),
    ]
    for scenario in sorted(table):
        cells = []
        for arm in arms:
            cell = table[scenario].get(arm)
            if cell is None:
                cells.append("-")
            else:
                blocks = ", ".join(str(b) for b in cell["blocks"])
                rate = cell["passed"] / cell["runs"] if cell["runs"] else 0.0
                cells.append(f"{cell['passed']}/{cell['runs']} {rate:.0%} ({blocks})")
        cells.extend(verdicts.get(scenario, {}).get(arm, "-") for arm in against)
        lines.append(f"`{scenario}` | " + " | ".join(cells))
    return "\n".join(lines)


def parse_specs(
    specs: Sequence[str],
) -> tuple[list[str], list[tuple[str, int | None, list[dict[str, Any]]]]]:
    """`arm=path` specs → (arms in first-seen order, (arm, block, records) per file)."""
    arms: list[str] = []
    files: list[tuple[str, int | None, list[dict[str, Any]]]] = []
    for spec in specs:
        arm, eq, path = spec.partition("=")
        if not eq or not arm:
            raise SystemExit(f"expected arm=path, got {spec!r}")
        if arm not in arms:
            arms.append(arm)
        with Path(path).open(encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        match = _BLOCK.search(Path(path).name)
        files.append((arm, int(match.group(1)) if match else None, records))
    return arms, files


def report(specs: Sequence[str]) -> str:
    """The joined table for `arm=path` specs."""
    arms, files = parse_specs(specs)
    table = aggregate((arm, records) for arm, _, records in files)
    return format_table(table, arms, paired(sample_outcomes(files), arms))


def main(argv: Sequence[str] | None = None) -> int:
    specs = list(sys.argv[1:] if argv is None else argv)
    if not specs:
        print(__doc__)
        return 2
    print(report(specs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
