"""Unit tests for the multi-turn eval helpers (``tests/eval_threads.py``)."""

from __future__ import annotations

from datetime import date, timedelta

from tests.eval_threads import changed_keys, mention_order, weekday_targets

_DENTIST = {
    1: ("Book dentist appointment", "appointment"),
    2: ("Pay the dentist bill", "bill"),
}


def test_mention_order_follows_the_reply_not_the_ids() -> None:
    reply = 'Two fit: "Pay the dentist bill" and "Book dentist appointment". Which one?'
    assert mention_order(reply, _DENTIST) == [2, 1]


def test_mention_order_matches_a_distinctive_word() -> None:
    reply = "Do you mean the appointment one, or the bill?"
    assert mention_order(reply, _DENTIST) == [1, 2]


def test_mention_order_places_a_candidate_at_its_first_mention() -> None:
    # The bill is named twice; its first mention is after the appointment's.
    reply = "The appointment or the bill? (The bill is overdue.)"
    assert mention_order(reply, _DENTIST) == [1, 2]
    reply = "The bill is overdue. Booking the appointment or paying the bill?"
    assert mention_order(reply, _DENTIST) == [2, 1]


def test_mention_order_leaves_out_what_is_never_named() -> None:
    assert mention_order("I found the appointment task.", _DENTIST) == [1]
    assert mention_order("Which dentist task?", _DENTIST) == []


def test_mention_order_is_case_insensitive_whole_words_and_folds_quotes() -> None:
    reply = "“BOOK DENTIST APPOINTMENT” — or the billing one?"
    # "billing" is not the word "bill".
    assert mention_order(reply, _DENTIST) == [1]


def test_mention_order_matches_titles_across_line_breaks() -> None:
    reply = "1. Pay the dentist\nbill\n2. Book dentist appointment"
    titles = {1: ("Book dentist appointment",), 2: ("Pay the dentist bill",)}
    assert mention_order(reply, titles) == [2, 1]


def test_weekday_targets_is_the_next_one_strictly_ahead() -> None:
    tuesday = date(2026, 10, 20)
    assert tuesday.weekday() == 1
    assert weekday_targets(tuesday, 4) == {date(2026, 10, 23)}
    saturday = date(2026, 10, 24)
    assert weekday_targets(saturday, 4) == {date(2026, 10, 30)}
    thursday = date(2026, 10, 22)
    assert weekday_targets(thursday, 4) == {date(2026, 10, 23)}


def test_weekday_targets_on_that_weekday_accepts_today_or_next_week() -> None:
    friday = date(2026, 10, 23)
    assert weekday_targets(friday, 4) == {friday, friday + timedelta(days=7)}


def test_changed_keys_sees_edits_additions_and_removals() -> None:
    before = {1: ("a", "medium"), 2: ("b", "medium"), 3: ("c", "low")}
    after = {1: ("a", "high"), 2: ("b", "medium"), 4: ("d", "medium")}
    assert changed_keys(before, after) == {1, 3, 4}
    assert changed_keys(before, dict(before)) == set()
