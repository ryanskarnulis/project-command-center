"""The frontier tier's pure parts, off the GPU (#408).

``tests/frontier.py`` grades, fingerprints and counts; this is its spec. No
model, no app: the episodes are plain objects.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tests.claims import Claim, Tally
from tests.frontier import (
    CHECKPOINT_MODES,
    COLLATERAL,
    FAILURE_MODES,
    FROM_MEMORY,
    MISSING_WRITE,
    STOPPED,
    UNBACKED_CLAIM,
    WORDINGS_PER_SPLIT,
    WRONG_VALUE,
    Checkpoint,
    FrontierResult,
    Grade,
    Scenario,
    ScenarioError,
    Variant,
    deal,
    failure_mode,
    grade,
    missed_all,
)

POOL = tuple((f"ask number {n}",) for n in range(2 * WORDINGS_PER_SPLIT))


def _checkpoints() -> tuple[Checkpoint, ...]:
    return (
        Checkpoint("wrote", lambda e: e["wrote"], MISSING_WRITE),
        Checkpoint("right_value", lambda e: e["value"] == 3, WRONG_VALUE),
        Checkpoint("nothing_else", lambda e: not e["stray"], COLLATERAL),
    )


def _scenario(**overrides: Any) -> Scenario:
    fields: dict[str, Any] = {
        "name": "demo",
        "tier": 2,
        "why": "a demo",
        "wordings": POOL,
        "checkpoints": _checkpoints(),
    }
    fields.update(overrides)
    return Scenario(**fields)


# --- scenarios and splits ----------------------------------------------------------


def test_deal_splits_the_pool_in_two_disjoint_halves() -> None:
    dev, heldout = deal("demo", POOL)
    assert len(dev) == len(heldout) == WORDINGS_PER_SPLIT
    assert {v.asks for v in dev}.isdisjoint({v.asks for v in heldout})
    assert {v.asks for v in (*dev, *heldout)} == set(POOL)
    # Named by pool position, so a wording keeps its name whichever side it lands.
    assert {v.name for v in (*dev, *heldout)} == {f"w{n:02d}" for n in range(len(POOL))}


def test_deal_is_seeded_by_the_scenario_name() -> None:
    assert deal("demo", POOL) == deal("demo", POOL)
    assert deal("demo", POOL) != deal("another", POOL)


def test_deal_refuses_a_pool_of_the_wrong_size() -> None:
    with pytest.raises(ValueError, match="want 10"):
        deal("demo", POOL[:-1])


def test_variants_by_split() -> None:
    scenario = _scenario()
    assert scenario.variants("dev") == scenario.dev
    assert scenario.variants("heldout") == scenario.heldout
    with pytest.raises(ValueError, match="unknown split"):
        scenario.variants("train")


def test_a_variant_needs_an_ask() -> None:
    with pytest.raises(ValueError, match="empty ask"):
        Variant("w00", ("  ",))


def test_a_checkpoint_names_a_known_mode() -> None:
    with pytest.raises(ValueError, match="unknown mode"):
        Checkpoint("x", lambda e: True, "flaky")
    # A stop is read off the whole sample, never named by a checkpoint.
    assert STOPPED not in CHECKPOINT_MODES
    assert set(CHECKPOINT_MODES) < set(FAILURE_MODES)


# --- fingerprints --------------------------------------------------------------------


def test_the_fingerprint_is_stable_and_per_split() -> None:
    scenario = _scenario()
    assert scenario.fingerprint("heldout") == _scenario().fingerprint("heldout")
    assert scenario.fingerprint("heldout") != scenario.fingerprint("dev")
    assert len(scenario.fingerprint("dev")) == 12


@pytest.mark.parametrize(
    "change",
    [
        {"wordings": (("a reworded ask",), *POOL[1:])},
        {"checkpoints": _checkpoints()[:2]},
        {"revision": 2},
        {"history": "live_like"},
    ],
    ids=["wording", "checkpoints", "revision", "history"],
)
def test_the_fingerprint_moves_with_what_was_measured(change: dict[str, Any]) -> None:
    before = _scenario()
    after = _scenario(**change)
    assert {before.fingerprint(s) for s in ("dev", "heldout")} != {
        after.fingerprint(s) for s in ("dev", "heldout")
    }


def test_the_fingerprint_ignores_what_was_not_measured() -> None:
    before = _scenario()
    after = replace(before, tier=3, why="reworded rationale", notes="moved from the gate")
    for split in ("dev", "heldout"):
        assert before.fingerprint(split) == after.fingerprint(split)


# --- grading -------------------------------------------------------------------------


def test_grade_scores_each_checkpoint() -> None:
    verdict = grade(
        {"wrote": True, "value": 4, "stray": False}, _checkpoints(), stopped=False, silent=False
    )
    assert verdict.hits == {"wrote": True, "right_value": False, "nothing_else": True}
    assert verdict.score == pytest.approx(2 / 3)
    assert not verdict.whole
    assert verdict.missed == ["right_value"]
    assert verdict.failure_mode == WRONG_VALUE


def test_a_whole_pass_needs_every_checkpoint_and_every_run_completed() -> None:
    episode = {"wrote": True, "value": 3, "stray": False}
    assert grade(episode, _checkpoints(), stopped=False, silent=False).whole
    stopped = grade(episode, _checkpoints(), stopped=True, silent=False)
    assert not stopped.whole and stopped.score == 1.0 and stopped.failure_mode == STOPPED
    assert grade(episode, _checkpoints(), stopped=False, silent=False).failure_mode is None


def test_a_checkpoint_that_raises_is_a_broken_scenario() -> None:
    broken = (Checkpoint("boom", lambda e: e["missing"], MISSING_WRITE),)
    with pytest.raises(ScenarioError, match="checkpoint boom raised"):
        grade({}, broken, stopped=False, silent=False)


@pytest.mark.parametrize(
    ("missed", "stopped", "silent", "want"),
    [
        ([], False, False, None),
        ([], True, False, STOPPED),
        (["nothing_else"], True, True, STOPPED),
        (["wrote", "nothing_else"], False, True, FROM_MEMORY),
        (["wrote", "nothing_else"], False, False, MISSING_WRITE),
        (["nothing_else", "wrote"], False, False, COLLATERAL),
        (["unknown"], False, False, MISSING_WRITE),
    ],
)
def test_failure_mode_precedence(
    missed: list[str], stopped: bool, silent: bool, want: str | None
) -> None:
    modes = {"wrote": MISSING_WRITE, "nothing_else": COLLATERAL}
    assert failure_mode(missed, modes, stopped=stopped, silent=silent) == want


def test_missed_all_is_a_stopped_zero() -> None:
    verdict = missed_all(_checkpoints())
    assert verdict.score == 0.0 and not verdict.whole
    assert verdict.failure_mode == STOPPED


# --- the result ----------------------------------------------------------------------


def _result() -> FrontierResult:
    scenario = _scenario()
    result = FrontierResult(
        scenario=scenario.name,
        tier=scenario.tier,
        split="heldout",
        checkpoints=tuple(c.name for c in scenario.checkpoints),
        fingerprint=scenario.fingerprint("heldout"),
    )
    hits = {"wrote": True, "right_value": True, "nothing_else": True}
    modes = {c.name: c.mode for c in scenario.checkpoints}
    result.add(Grade(hits, modes), {"variant": "w01", "seed": 7})
    result.add(Grade({**hits, "right_value": False}, modes), {"variant": "w03", "seed": 8})
    result.add(Grade({**hits, "wrote": False}, modes, silent=True), {"variant": "w01", "seed": 9})
    return result


def test_the_result_counts_passes_rubric_hits_and_modes() -> None:
    result = _result()
    assert (result.passed, result.runs) == (1, 3)
    assert result.rubric == pytest.approx((1 + 2 / 3 + 2 / 3) / 3)
    assert result.checkpoint_hits == {"wrote": 2, "right_value": 2, "nothing_else": 3}
    assert result.failure_modes == {WRONG_VALUE: 1, FROM_MEMORY: 1}
    assert result.variant_passes == {
        "w01": {"passed": 1, "runs": 2},
        "w03": {"passed": 0, "runs": 1},
    }
    low, high = result.interval
    assert 0.0 <= low < 1 / 3 < high <= 1.0


def test_the_record_carries_what_the_history_reads() -> None:
    result = _result()
    result.claims.add([Claim("created", "I added it.", "added", backed=False)])
    record = result.record(why="a demo")
    assert record["kind"] == "frontier" and record["why"] == "a demo"
    for key in (
        "scenario",
        "tier",
        "split",
        "fingerprint",
        "runs",
        "passed",
        "interval",
        "rubric",
        "checkpoint_hits",
        "failure_modes",
        "variants",
        "infra",
        "claims",
        "samples",
    ):
        assert key in record, key
    assert record["claims"]["made"] == 1 and record["claims"]["backed"] == 0
    first = record["samples"][0]
    assert first["variant"] == "w01" and first["whole"] is True and first["failure_mode"] is None
    assert record["samples"][2]["failure_mode"] == FROM_MEMORY
    assert "claims 0/1" in result.summary()


def test_an_empty_result_measured_nothing() -> None:
    result = FrontierResult("demo", 1, "dev", ("wrote",), "abc")
    assert result.rubric is None and result.runs == 0
    assert result.interval == (0.0, 1.0)
    assert "0/0 whole" in result.summary() and "rubric —" in result.summary()
    assert result.claims.as_dict()["made"] == 0
    assert isinstance(result.claims, Tally)
    assert UNBACKED_CLAIM in CHECKPOINT_MODES
