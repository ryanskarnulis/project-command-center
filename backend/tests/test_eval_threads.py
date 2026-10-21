"""Unit tests for the multi-turn eval helpers (``tests/eval_threads.py``)."""

from __future__ import annotations

from datetime import date, timedelta

from tests.eval_threads import (
    changed_keys,
    end_of_month,
    mention_order,
    next_friday_targets,
    next_month_bounds,
    weekday_targets,
    weekend_targets,
)

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


def test_next_friday_accepts_the_coming_friday_and_next_weeks() -> None:
    tuesday = date(2026, 10, 20)
    assert next_friday_targets(tuesday) == {date(2026, 10, 23), date(2026, 10, 30)}
    monday = date(2026, 10, 19)
    assert next_friday_targets(monday) == {date(2026, 10, 23), date(2026, 10, 30)}
    thursday = date(2026, 10, 22)
    assert next_friday_targets(thursday) == {date(2026, 10, 23), date(2026, 10, 30)}


def test_next_friday_from_the_weekend_and_a_friday_is_one_reading() -> None:
    assert next_friday_targets(date(2026, 10, 24)) == {date(2026, 10, 30)}  # Sat
    assert next_friday_targets(date(2026, 10, 25)) == {date(2026, 10, 30)}  # Sun
    friday = date(2026, 10, 23)
    assert next_friday_targets(friday) == {friday + timedelta(days=7)}  # never today


def test_next_friday_crosses_month_and_year_ends() -> None:
    assert next_friday_targets(date(2026, 12, 29)) == {date(2027, 1, 1), date(2027, 1, 8)}


def test_end_of_month_handles_lengths_and_leap_years() -> None:
    assert end_of_month(date(2026, 10, 20)) == date(2026, 10, 31)
    assert end_of_month(date(2026, 11, 3)) == date(2026, 11, 30)
    assert end_of_month(date(2028, 2, 10)) == date(2028, 2, 29)
    assert end_of_month(date(2026, 2, 10)) == date(2026, 2, 28)
    assert end_of_month(date(2026, 12, 31)) == date(2026, 12, 31)


def test_weekend_targets_midweek_is_the_coming_weekend() -> None:
    for today in (date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 23)):
        assert weekend_targets(today) == {date(2026, 10, 24), date(2026, 10, 25)}


def test_weekend_targets_on_the_weekend_keeps_today_and_the_next_one() -> None:
    saturday = date(2026, 10, 24)
    assert weekend_targets(saturday) == {
        saturday,
        date(2026, 10, 25),
        date(2026, 10, 31),
        date(2026, 11, 1),
    }
    sunday = date(2026, 10, 25)
    assert weekend_targets(sunday) == {sunday, date(2026, 10, 31), date(2026, 11, 1)}


def test_weekend_targets_are_only_ever_weekend_days_not_in_the_past() -> None:
    start = date(2026, 1, 1)
    for offset in range(400):
        today = start + timedelta(days=offset)
        targets = weekend_targets(today)
        assert targets and all(d >= today and d.weekday() >= 5 for d in targets)


def test_next_month_bounds_is_the_whole_next_calendar_month() -> None:
    assert next_month_bounds(date(2026, 10, 20)) == (date(2026, 11, 1), date(2026, 11, 30))
    assert next_month_bounds(date(2026, 12, 31)) == (date(2027, 1, 1), date(2027, 1, 31))
    assert next_month_bounds(date(2026, 1, 31)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert next_month_bounds(date(2027, 1, 31)) == (date(2027, 2, 1), date(2027, 2, 28))
