"""The frontier tier, live (#408): hard asks, measured and never gated.

Opt-in like the gate, and never in CI (it needs the model):

    cd backend
    PCC_AGENT_FRONTIER=1 PCC_AGENT_EVALS_REPORT=/tmp/frontier.jsonl \\
        .venv/bin/pytest tests/test_agent_frontier.py -v -s
    .venv/bin/python scripts/frontier_report.py append /tmp/frontier.jsonl --label "..."

A scenario's pass count is **reported, not asserted**: a low number is the
measurement this tier exists for. An item fails only when the number could not
be measured or cannot be trusted: no sample measured (every one an
infrastructure death, or a request llama-server rejected), a checkpoint that
raised (``frontier.ScenarioError``), or a bug in the harness.

Knobs: ``PCC_FRONTIER_SPLIT`` (``heldout``, the default and the number of
record, or ``dev`` when iterating on a prompt change), ``PCC_FRONTIER_RUNS``
(samples per scenario, default one per wording), and the gate's own
``PCC_AGENT_EVALS_SEED`` (paired arms) and ``PCC_AGENT_EVALS_REPORT``. The
report opens with a ``frontier_header`` naming the build, model, profile and
the prompt and tool-schema hashes, so each number is tied to what produced
it; each scenario's ``frontier`` line names its split's fingerprint.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import UTC, datetime

import pytest

from app.ai.providers.llamacpp import provider_from_settings
from tests.eval_report import int_from_env, write_jsonl
from tests.frontier import SPLITS, WORDINGS_PER_SPLIT, Scenario
from tests.frontier_corpus import SCENARIOS, measure
from tests.test_agent_evals import _header

pytestmark = pytest.mark.skipif(
    os.environ.get("PCC_AGENT_FRONTIER") != "1",
    reason="the frontier tier runs the real model: set PCC_AGENT_FRONTIER=1",
)

SPLIT = os.environ.get("PCC_FRONTIER_SPLIT", "heldout")
RUNS = int_from_env("PCC_FRONTIER_RUNS", WORDINGS_PER_SPLIT) or WORDINGS_PER_SPLIT
_SEED_BASE = int_from_env("PCC_AGENT_EVALS_SEED", None, minimum=0)
_REPORT_PATH = os.environ.get("PCC_AGENT_EVALS_REPORT") or None


@pytest.fixture(scope="module", autouse=True)
def _frontier_header() -> Generator[None, None, None]:
    if SPLIT not in SPLITS:
        pytest.fail(f"PCC_FRONTIER_SPLIT={SPLIT!r}: heldout or dev")
    with provider_from_settings() as provider:
        gate = _header(provider)
    header = {
        **{k: v for k, v in gate.items() if k not in ("kind", "knobs", "floors")},
        "kind": "frontier_header",
        "started": datetime.now(UTC).isoformat(),
        "split": SPLIT,
        "runs": RUNS,
        "seed": _SEED_BASE,
    }
    print(f"\n[frontier] header {json.dumps(header)}")
    write_jsonl(_REPORT_PATH, header)
    yield


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_frontier(scenario: Scenario) -> None:
    result = measure(scenario, runs=RUNS, split=SPLIT, seed_base=_SEED_BASE)
    print(
        f"\n[frontier] scenario={scenario.name} tier={scenario.tier} split={SPLIT} "
        f"fingerprint={result.fingerprint} {result.summary()}"
    )
    for name, hits in result.checkpoint_hits.items():
        print(f"[frontier]   {hits}/{result.runs} {name}")
    for name, counts in result.variant_passes.items():
        print(f"[frontier]   {counts['passed']}/{counts['runs']} wording {name}")
    for mode, count in result.failure_modes.most_common():
        print(f"[frontier]   ×{count} {mode}")
    for miss in result.claims.misses:
        print(f"[frontier]   ✗ unbacked [{miss['family']}] {miss['line']!r}")
    write_jsonl(_REPORT_PATH, result.record(why=scenario.why))
    if result.runs == 0:
        pytest.fail(
            f"{scenario.name}: no sample was measured ({result.infra} infrastructure "
            "deaths or a rejected request): llama-server is not answering"
        )
