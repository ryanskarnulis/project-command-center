"""Pure helpers for the multi-turn eval scenarios (#389, #405).

Kept out of ``test_agent_evals.py`` (which is skipped unless the real model
is in play) so CI unit-tests them: ``tests/test_eval_threads.py``.

* :func:`mention_order` is the rule that defines "the first one" and "the
  second one". Conversation history is text only (tool calls and results are
  never round-tripped, ``services/conversations.history_for_loop``), so the
  list the model sees on the next turn is exactly its own reply's words. The
  n-th one is therefore the n-th candidate *mentioned* in that reply, ordered
  by where each candidate is first named.
* :func:`weekday_targets` is what "Friday" means on a given day.
* :func:`changed_keys` is the "nothing else changed" diff over snapshots.
* The date readings of #406 (:func:`next_friday_targets`,
  :func:`end_of_month`, :func:`weekend_targets`, :func:`next_month_bounds`)
  are what "next Friday", "end of the month", "this weekend" and "next
  month" may mean on a given day. Each accepts every reasonable English
  reading, so a miss is the model's arithmetic and never the scenario's
  opinion; ``today`` is always the real date, never a constant.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Mapping, Sequence
import calendar
from datetime import date, timedelta
from typing import TypeVar

K = TypeVar("K", bound=Hashable)

# Curly quotes and apostrophes a reply may use, folded to ASCII before matching.
_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _first_position(text: str, phrases: Sequence[str]) -> int | None:
    """Where the earliest of ``phrases`` first appears in ``text`` (whole
    words, case-insensitive), or None if none does."""
    positions = []
    for phrase in phrases:
        pattern = r"\b" + r"\s+".join(re.escape(word) for word in phrase.split()) + r"\b"
        match = re.search(pattern, text, re.IGNORECASE)
        if match is not None:
            positions.append(match.start())
    return min(positions) if positions else None


def mention_order(text: str, candidates: Mapping[K, Sequence[str]]) -> list[K]:
    """The candidates ``text`` names, in the order it first names them.

    ``candidates`` maps each candidate (a task id, say) to the phrases that
    identify it: its title and any word only it carries ("appointment" for
    "Book dentist appointment" beside "Pay the dentist bill"). A candidate is
    placed at the earliest position any of its phrases matches; candidates the
    text never names are left out, so ``len(result)`` is how many it named.
    Ties (two candidates matched at one position) keep ``candidates`` order.
    """
    folded = text.translate(_FOLD)
    placed: list[tuple[int, int, K]] = []
    for rank, (key, phrases) in enumerate(candidates.items()):
        position = _first_position(folded, phrases)
        if position is not None:
            placed.append((position, rank, key))
    return [key for _, _, key in sorted(placed)]


def weekday_targets(today: date, weekday: int) -> frozenset[date]:
    """The dates "push it to <weekday>" may mean, said on ``today``.

    ``weekday`` is ``date.weekday()``'s (Monday 0 … Sunday 6). Always the next
    such day strictly after today (1–7 days ahead). Said *on* that weekday the
    words are ambiguous ("Friday" on a Friday), so today is accepted as well.
    """
    ahead = (weekday - today.weekday()) % 7 or 7
    targets = {today + timedelta(days=ahead)}
    if today.weekday() == weekday:
        targets.add(today)
    return frozenset(targets)


def changed_keys(before: Mapping[K, object], after: Mapping[K, object]) -> set[K]:
    """Keys added, removed, or whose value differs between two snapshots."""
    return {
        key
        for key in before.keys() | after.keys()
        if key not in before or key not in after or before[key] != after[key]
    }


FRIDAY = 4  # date.weekday()


def next_friday_targets(today: date) -> frozenset[date]:
    """The dates "next Friday" may mean, said on ``today`` (#406).

    English is genuinely split: some speakers mean the coming Friday, others
    the Friday of *next week*. Both are accepted: the next Friday strictly
    after today, and the Friday of the following Monday-to-Sunday week. On a
    Tuesday those are 3 and 10 days ahead; on a Saturday or Sunday they
    coincide (the coming Friday *is* next week's), as they do on a Friday
    (today + 7). Today itself is never accepted, unlike
    :func:`weekday_targets`: nobody says "next Friday" on a Friday to mean
    today.
    """
    coming = today + timedelta(days=(FRIDAY - today.weekday()) % 7 or 7)
    monday_next_week = today + timedelta(days=7 - today.weekday())
    return frozenset({coming, monday_next_week + timedelta(days=FRIDAY)})


def end_of_month(today: date) -> date:
    """The last day of ``today``'s month ("by the end of the month")."""
    return today.replace(day=calendar.monthrange(today.year, today.month)[1])


def weekend_targets(today: date) -> frozenset[date]:
    """The dates "this weekend" may mean, said on ``today`` (#406).

    Saturday or Sunday of the current weekend or the upcoming one, minus
    anything already past. Monday to Friday that is the coming Saturday and
    Sunday. On a Saturday: today, tomorrow and the next weekend's two days;
    on a Sunday: today and the next weekend's two days (yesterday is gone).
    """
    reach = 6 if today.weekday() < 5 else 8
    days = (today + timedelta(days=ahead) for ahead in range(reach + 1))
    return frozenset(day for day in days if day.weekday() >= 5)


def next_month_bounds(today: date) -> tuple[date, date]:
    """First and last day of the calendar month after ``today``'s.

    "Next month" (a follow-up due then) is accepted as *any* date in that
    month, which also holds "the same day one month on" and "the 1st", the
    two readings people use (#406).
    """
    first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first, end_of_month(first)
