"""The frontier corpus's shape and wiring, off the GPU (#408).

Every scenario is played here over a scripted provider: one that does nothing
(the seed, the stored history, the route and every checkpoint must run, and
doing nothing must not pass), and, for two scenarios, an oracle that does the
task (a whole pass must be reachable, and ``measure`` must report it).
"""

from __future__ import annotations

import string
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest

from app.ai.providers.llamacpp import ChatResult
from tests.frontier import (
    DID_NOT_ASK,
    FROM_MEMORY,
    MISSING_WRITE,
    WORDINGS_PER_SPLIT,
    Scenario,
)
from tests.frontier_corpus import (
    AMBIGUOUS_THEN_PICK,
    HISTORIES,
    POISONED_COMPLETE,
    SCENARIOS,
    all_wordings,
    date_slots,
    measure,
    play,
)
from tests.scripted_provider import ScriptedProvider, text_turn, tool_calls_turn
from tests.test_agent_evals import Sample


def _script(monkeypatch: pytest.MonkeyPatch, turns: Sequence[ChatResult]) -> None:
    """Every sample's provider plays ``turns`` (a fresh copy each sample)."""

    @contextmanager
    def scripted(_self: Sample) -> Iterator[ScriptedProvider]:
        yield ScriptedProvider(list(turns))

    monkeypatch.setattr(Sample, "provider", scripted)


# --- shape -------------------------------------------------------------------------


def test_names_are_unique_and_tiers_known() -> None:
    names = [s.name for s in SCENARIOS]
    assert len(names) == len(set(names))
    assert {s.tier for s in SCENARIOS} <= {1, 2, 3}
    for scenario in SCENARIOS:
        checkpoints = [c.name for c in scenario.checkpoints]
        assert len(checkpoints) == len(set(checkpoints)), scenario.name
        assert "claims_backed" in checkpoints, f"{scenario.name} must read its replies"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_each_split_has_its_own_wordings(scenario: Scenario) -> None:
    dev, heldout = scenario.dev, scenario.heldout
    assert len(dev) == len(heldout) == WORDINGS_PER_SPLIT
    assert {v.asks for v in dev}.isdisjoint({v.asks for v in heldout})
    turns = {len(v.asks) for v in (*dev, *heldout)}
    assert len(turns) == 1, f"{scenario.name}: every wording has the same turns"


def test_every_ask_formats_with_todays_slots() -> None:
    slots = date_slots(date.today())
    for scenario, _split, variant in all_wordings(SCENARIOS):
        for ask in variant.asks:
            fields = {name for _, name, _, _ in string.Formatter().parse(ask) if name}
            assert fields <= slots.keys(), f"{scenario.name}/{variant.name}: {fields}"
            assert "{" not in ask.format(**slots)


def test_histories_resolve() -> None:
    for scenario in SCENARIOS:
        if scenario.history is not None:
            history = HISTORIES[scenario.history](date.today())
            assert len(history) == 17, "16 live-like turns plus the poison"


def test_moved_gate_scenarios_keep_their_issues() -> None:
    moved = {s.name: s.notes for s in SCENARIOS if "Moved from the gate" in s.notes}
    assert set(moved) == {
        "poisoned_complete",
        "poisoned_reschedule",
        "poisoned_followup",
        "ambiguous_then_pick",
        "misheard_push",
    }
    for name, notes in moved.items():
        assert any(issue in notes for issue in ("#412", "#416", "#419", "#420")), name


# --- wiring, over a scripted provider -------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_doing_nothing_plays_grades_and_misses(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(monkeypatch, [text_turn("Okay.") for _ in range(10)])
    variant = scenario.heldout[0]
    taken = play(scenario, variant, 1, None)
    assert taken.error is None, repr(taken.error)
    assert taken.grade is not None
    assert len(taken.sample.steps) == len(variant.asks)
    assert not taken.grade.whole, f"{scenario.name} passes by doing nothing"
    assert taken.grade.failure_mode in (FROM_MEMORY, DID_NOT_ASK, MISSING_WRITE)


def test_the_poisoned_complete_oracle_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    # _seed_world: Home, Work, Errands; "Book dentist appointment" is task 1.
    _script(
        monkeypatch,
        [
            tool_calls_turn(("complete_task", {"task_id": 1})),
            text_turn('Marked "Book dentist appointment" as done.'),
        ],
    )
    result = measure(POISONED_COMPLETE, runs=2, split="heldout", seed_base=100, log=lambda _: None)
    assert (result.passed, result.runs, result.infra) == (2, 2, 0)
    assert result.rubric == 1.0 and not result.failure_modes
    assert result.claims.scored_made >= 1 and not result.claims.misses
    record: dict[str, Any] = result.record()
    assert [s["variant"] for s in record["samples"]] == [
        v.name for v in POISONED_COMPLETE.heldout[:2]
    ]
    assert [s["seed"] for s in record["samples"]] == [100, 101]
    assert record["fingerprint"] == POISONED_COMPLETE.fingerprint("heldout")
    # What the sample changed, for reading a row by hand.
    assert record["samples"][0]["changes"] == {
        "tasks": {"1 Book dentist appointment": {"status": ["open", "done"]}},
        "projects": {},
    }


def test_the_ambiguous_oracle_asks_then_picks_the_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tasks: 1 "Book dentist appointment", 2 "Pay the dentist bill". The
    # question names the bill first, so "the second one" is the appointment.
    _script(
        monkeypatch,
        [
            tool_calls_turn(("search", {"query": "dentist"})),
            text_turn('Which one: "Pay the dentist bill" or "Book dentist appointment"?'),
            tool_calls_turn(("complete_task", {"task_id": 1})),
            text_turn('Marked "Book dentist appointment" as done.'),
        ],
    )
    taken = play(AMBIGUOUS_THEN_PICK, AMBIGUOUS_THEN_PICK.heldout[0], 1, None)
    assert taken.grade is not None
    assert taken.grade.hits == {name: True for name in taken.grade.hits}, taken.grade.missed
    assert taken.grade.whole


def test_a_wrong_pick_is_a_wrong_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _script(
        monkeypatch,
        [
            tool_calls_turn(("search", {"query": "dentist"})),
            text_turn('Which one: "Pay the dentist bill" or "Book dentist appointment"?'),
            tool_calls_turn(("complete_task", {"task_id": 2})),
            text_turn('Marked "Pay the dentist bill" as done.'),
        ],
    )
    taken = play(AMBIGUOUS_THEN_PICK, AMBIGUOUS_THEN_PICK.heldout[0], 1, None)
    assert taken.grade is not None
    assert taken.grade.missed == ["second_done", "only_the_second"]
    assert taken.grade.failure_mode == "wrong_target"
