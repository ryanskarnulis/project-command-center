"""Unit tests for the pure dependency auto-shift core (Slice 5).

``compute_shifts`` is side-effect free, so it's exercised in isolation here (the
DB-facing cascade is covered through the PATCH route in ``test_planning``). Mirrors
the frontend's ``dependencyConflicts`` tests: finish-to-start means a dependent
must start no earlier than ``blocker.end + 1``.
"""

from datetime import date

from app.services.planning import Placement, compute_shifts


def _placement(start: date | None, minutes: int | None = 480) -> Placement:
    return Placement(scheduled_start=start, estimated_minutes=minutes)


def test_span_days_rounds_up_and_floors_at_one() -> None:
    assert _placement(None, None).span_days == 1
    assert _placement(None, 1).span_days == 1
    assert _placement(None, 480).span_days == 1
    assert _placement(None, 481).span_days == 2
    assert _placement(None, 960).span_days == 2


def test_end_is_inclusive_last_day() -> None:
    # 2-day span starting the 20th -> ends the 21st (inclusive).
    assert _placement(date(2026, 6, 20), 960).end() == date(2026, 6, 21)
    assert _placement(None).end() is None


def test_shifts_dependent_that_starts_before_blocker_finishes() -> None:
    # Blocker spans the 20th-21st; dependent starts the 21st -> must move to 22nd.
    placements = {
        1: _placement(date(2026, 6, 20), 960),
        2: _placement(date(2026, 6, 21), 480),
    }
    shifts = compute_shifts(placements, [(2, 1)])
    assert shifts == {2: date(2026, 6, 22)}


def test_shifts_dependent_that_starts_exactly_on_blocker_end() -> None:
    placements = {
        1: _placement(date(2026, 6, 20), 480),  # ends 20th
        2: _placement(date(2026, 6, 20), 480),
    }
    assert compute_shifts(placements, [(2, 1)]) == {2: date(2026, 6, 21)}


def test_leaves_dependent_that_already_starts_late_enough() -> None:
    placements = {
        1: _placement(date(2026, 6, 20), 480),  # ends 20th
        2: _placement(date(2026, 6, 21), 480),  # already starts the 21st
    }
    assert compute_shifts(placements, [(2, 1)]) == {}


def test_cascades_through_a_chain() -> None:
    # A -> B -> C, all single-day, all stacked on the 20th. Pushing the chain:
    # B moves to the 21st, then C (which depends on B's new end) to the 22nd.
    placements = {
        1: _placement(date(2026, 6, 20)),
        2: _placement(date(2026, 6, 20)),
        3: _placement(date(2026, 6, 20)),
    }
    # edges: (dependent, blocker)
    shifts = compute_shifts(placements, [(2, 1), (3, 2)])
    assert shifts == {2: date(2026, 6, 21), 3: date(2026, 6, 22)}


def test_dependent_with_two_blockers_takes_the_latest() -> None:
    # C depends on both A (ends 21st) and B (ends 23rd) -> must start the 24th.
    placements = {
        1: _placement(date(2026, 6, 20), 960),  # ends 21st
        2: _placement(date(2026, 6, 22), 960),  # ends 23rd
        3: _placement(date(2026, 6, 20), 480),
    }
    shifts = compute_shifts(placements, [(3, 1), (3, 2)])
    assert shifts == {3: date(2026, 6, 24)}


def test_unscheduled_dependent_does_not_move() -> None:
    placements = {
        1: _placement(date(2026, 6, 20)),
        2: _placement(None),  # no bar -> no shift
    }
    assert compute_shifts(placements, [(2, 1)]) == {}


def test_unscheduled_blocker_imposes_no_constraint() -> None:
    placements = {
        1: _placement(None),  # blocker has no end to anchor to
        2: _placement(date(2026, 6, 20)),
    }
    assert compute_shifts(placements, [(2, 1)]) == {}


def test_diamond_dependency_shifts_each_node_once() -> None:
    #   A
    #  / \        B and C depend on A; D depends on both. All single-day on the 20th.
    # B   C       A stays; B,C -> 21st; D -> 22nd (after both B and C land).
    #  \ /
    #   D
    placements = {n: _placement(date(2026, 6, 20)) for n in (1, 2, 3, 4)}
    shifts = compute_shifts(placements, [(2, 1), (3, 1), (4, 2), (4, 3)])
    assert shifts == {
        2: date(2026, 6, 21),
        3: date(2026, 6, 21),
        4: date(2026, 6, 22),
    }
