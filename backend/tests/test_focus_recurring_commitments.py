"""Repeating commitments on the Focus plan (#376): one series row, read per day."""

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, FocusPlanEntry
from app.services import focus as focus_service
from app.services import focus_plan as focus_plan_service
from app.services import tasks as tasks_service

START = date(2026, 6, 20)  # a Saturday
DAILY = {"unit": "day", "every": 1}
WEEKDAYS = {"unit": "week", "every": 1, "days": [0, 1, 2, 3, 4]}
EVERY_OTHER_TUE_THU = {"unit": "week", "every": 2, "days": [1, 3]}


def _series(
    db: Session,
    interval: dict[str, Any],
    *,
    start: date = START,
    title: str = "Standup",
    at: str = "10:00",
    minutes: int = 15,
) -> FocusPlanEntry:
    entry = focus_plan_service.add_commitment(
        db,
        plan_date=start,
        title=title,
        start_minute=focus_plan_service.parse_time(at),
        duration_minutes=minutes,
        repeat_interval=interval,
    )
    db.commit()
    return entry


def _titles_on(db: Session, day: date) -> list[str]:
    plan = focus_service.get_focus_plan(db, target_date=day, start_time="09:00")
    return [c.title for c in plan.commitments]


def _row_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(FocusPlanEntry)) or 0


# --- Which days a series covers --------------------------------------------------


@pytest.mark.parametrize(
    ("interval", "start", "day", "expected"),
    [
        (DAILY, START, START, True),
        (DAILY, START, START + timedelta(days=40), True),
        (DAILY, START, START - timedelta(days=1), False),
        ({"unit": "day", "every": 3}, START, START + timedelta(days=6), True),
        ({"unit": "day", "every": 3}, START, START + timedelta(days=7), False),
        ({"unit": "week", "every": 1}, START, START + timedelta(days=14), True),
        ({"unit": "week", "every": 2}, START, START + timedelta(days=7), False),
        ({"unit": "week", "every": 2}, START, START + timedelta(days=14), True),
        # Monthly counts from the first occurrence: the 31st clamps to a short
        # month's last day and comes back to the 31st after it.
        ({"unit": "month", "every": 1}, date(2026, 1, 31), date(2026, 2, 28), True),
        ({"unit": "month", "every": 1}, date(2026, 1, 31), date(2026, 3, 31), True),
        ({"unit": "month", "every": 1}, date(2026, 1, 31), date(2026, 3, 28), False),
        ({"unit": "month", "every": 2}, date(2026, 1, 15), date(2026, 2, 15), False),
        ({"unit": "month", "every": 2}, date(2026, 1, 15), date(2026, 3, 15), True),
        # A weekday set (#427): Mon-Fri from a Saturday start skips the weekend.
        (WEEKDAYS, START, START, False),
        (WEEKDAYS, START, START + timedelta(days=1), False),
        (WEEKDAYS, START, START + timedelta(days=2), True),
        (WEEKDAYS, START, START + timedelta(days=6), True),
        (WEEKDAYS, START, START + timedelta(days=7), False),
        (WEEKDAYS, START, START - timedelta(days=2), False),
        # Mid-week start: nothing before the anchor, even in the same week.
        (WEEKDAYS, date(2026, 6, 17), date(2026, 6, 16), False),
        (WEEKDAYS, date(2026, 6, 17), date(2026, 6, 18), True),
        # Every other week counts calendar weeks from the anchor's week.
        (EVERY_OTHER_TUE_THU, date(2026, 6, 17), date(2026, 6, 18), True),
        (EVERY_OTHER_TUE_THU, date(2026, 6, 17), date(2026, 6, 23), False),
        (EVERY_OTHER_TUE_THU, date(2026, 6, 17), date(2026, 6, 30), True),
    ],
)
def test_occurs_on(
    interval: dict[str, Any], start: date, day: date, expected: bool
) -> None:
    series = FocusPlanEntry(plan_date=start, repeat_interval=interval)
    assert focus_plan_service.occurs_on(series, day) is expected


# --- The plan reads the series per day ----------------------------------------------


def test_a_daily_commitment_shows_on_a_later_day_without_new_rows(
    db_session: Session,
) -> None:
    series = _series(db_session, DAILY)
    rows = _row_count(db_session)
    later = START + timedelta(days=30)

    plan = focus_service.get_focus_plan(db_session, target_date=later, start_time="09:00")

    assert [(c.entry_id, c.title, c.start_time) for c in plan.commitments] == [
        (series.id, "Standup", "10:00")
    ]
    assert plan.commitments[0].repeat_interval is not None
    assert plan.commitments[0].repeat_interval.unit == "day"
    # Reading a day stores nothing: the occurrence is worked out, not materialised.
    assert _row_count(db_session) == rows
    assert _titles_on(db_session, START - timedelta(days=1)) == []


def test_tasks_pack_around_an_occurrence(db_session: Session) -> None:
    _series(db_session, DAILY, at="09:30", minutes=30)
    task = tasks_service.create_task(
        db_session, project_id=None, title="deep work", estimated_minutes=60
    )
    db_session.commit()

    plan = focus_service.get_focus_plan(
        db_session, target_date=START + timedelta(days=3), start_time="09:00"
    )

    assert [(b.task_id, b.start_time) for b in plan.scheduled] == [(task.id, "10:00")]


def test_skipping_one_occurrence_leaves_the_rest_of_the_series(
    db_session: Session,
) -> None:
    series = _series(db_session, DAILY)
    skipped_day = START + timedelta(days=2)

    skip = focus_plan_service.skip_occurrence(db_session, series, skipped_day)
    db_session.commit()

    assert _titles_on(db_session, skipped_day) == []
    assert _titles_on(db_session, skipped_day - timedelta(days=1)) == ["Standup"]
    assert _titles_on(db_session, skipped_day + timedelta(days=1)) == ["Standup"]
    # Idempotent: the day is already skipped.
    assert focus_plan_service.skip_occurrence(db_session, series, skipped_day).id == skip.id

    # Undo: removing the skip (a soft delete) brings the occurrence back...
    focus_plan_service.remove_entry(db_session, skip)
    db_session.commit()
    assert _titles_on(db_session, skipped_day) == ["Standup"]
    # ...and restoring the skip skips it again.
    focus_plan_service.restore_entry(db_session, skip)
    db_session.commit()
    assert _titles_on(db_session, skipped_day) == []


def test_removing_the_series_stops_every_day_and_restore_undoes_it(
    db_session: Session,
) -> None:
    series = _series(db_session, {"unit": "week", "every": 1})
    next_week = START + timedelta(days=7)

    focus_plan_service.remove_entry(db_session, series)
    db_session.commit()
    assert _titles_on(db_session, START) == []
    assert _titles_on(db_session, next_week) == []

    focus_plan_service.restore_entry(db_session, series)
    db_session.commit()
    assert _titles_on(db_session, next_week) == ["Standup"]


def test_editing_the_series_moves_every_occurrence(db_session: Session) -> None:
    series = _series(db_session, DAILY)
    focus_plan_service.update_entry(db_session, series, {"start_time": "11:30"})
    db_session.commit()

    plan = focus_service.get_focus_plan(
        db_session, target_date=START + timedelta(days=5), start_time="09:00"
    )
    assert [c.start_time for c in plan.commitments] == ["11:30"]


def test_skip_rules(db_session: Session) -> None:
    series = _series(db_session, {"unit": "week", "every": 1})
    one_off = focus_plan_service.add_commitment(
        db_session, plan_date=START, title="Dentist", start_minute=900, duration_minutes=60
    )
    db_session.commit()

    with pytest.raises(focus_plan_service.FocusEntryError):
        focus_plan_service.skip_occurrence(db_session, series, START + timedelta(days=1))
    with pytest.raises(focus_plan_service.FocusEntryError):
        focus_plan_service.skip_occurrence(db_session, one_off, START)

    skip = focus_plan_service.skip_occurrence(db_session, series, START)
    with pytest.raises(focus_plan_service.FocusEntryError):
        focus_plan_service.update_entry(db_session, skip, {"start_time": "12:00"})
    with pytest.raises(focus_plan_service.FocusEntryError):
        focus_plan_service.reorder_pins(db_session, plan_date=START, entry_ids=[skip.id])


def test_series_writes_are_audited(db_session: Session) -> None:
    series = _series(db_session, DAILY)
    skip = focus_plan_service.skip_occurrence(db_session, series, START)
    focus_plan_service.remove_entry(db_session, skip)
    db_session.commit()

    events = db_session.execute(
        select(ActivityEvent.action, ActivityEvent.summary)
        .where(ActivityEvent.entity_type == "focus_entry")
        .order_by(ActivityEvent.id)
    ).all()
    assert [action for action, _summary in events] == ["created", "skipped", "deleted"]
    assert "daily from 2026-06-20" in events[0][1]
    assert events[1][1] == 'Commitment "Standup" on 2026-06-20 skipped'


def test_a_weekday_series_reads_mon_to_fri_and_names_itself(db_session: Session) -> None:
    _series(db_session, WEEKDAYS)
    monday = START + timedelta(days=2)
    week = [_titles_on(db_session, monday + timedelta(days=n)) for n in range(7)]
    assert week == [["Standup"]] * 5 + [[], []]

    summary = db_session.execute(
        select(ActivityEvent.summary).where(ActivityEvent.entity_type == "focus_entry")
    ).scalar_one()
    assert "weekdays from 2026-06-20" in summary


@pytest.mark.parametrize(
    ("interval", "label"),
    [
        (DAILY, "daily"),
        ({"unit": "week", "every": 2}, "every 2 weeks"),
        (WEEKDAYS, "weekdays"),
        ({"unit": "week", "every": 1, "days": [0, 2, 4]}, "weekly on Mon, Wed, Fri"),
        (EVERY_OTHER_TUE_THU, "every 2 weeks on Tue, Thu"),
    ],
)
def test_format_repeat(interval: dict[str, Any], label: str) -> None:
    assert focus_plan_service.format_repeat(interval) == label


# --- Routes -----------------------------------------------------------------------


def test_route_adds_a_weekday_commitment(client: TestClient) -> None:
    created = client.post(
        "/api/focus/commitments",
        json={
            "plan_date": START.isoformat(),
            "title": "Standup",
            "start_time": "09:30",
            "duration_minutes": 15,
            # Out of order on the way in; stored and read back sorted.
            "repeat_interval": {"unit": "week", "every": 1, "days": [4, 3, 2, 1, 0]},
        },
    )
    assert created.status_code == 201
    assert created.json()["repeat_interval"] == WEEKDAYS

    def cadences_on(day: date) -> list[Any]:
        plan = client.get("/api/focus", params={"date": day.isoformat()}).json()
        return [c["repeat_interval"] for c in plan["commitments"]]

    assert cadences_on(START + timedelta(days=1)) == []  # Sunday
    assert cadences_on(START + timedelta(days=3)) == [WEEKDAYS]  # Tuesday


def test_routes_add_a_daily_commitment_and_skip_a_day(client: TestClient) -> None:
    created = client.post(
        "/api/focus/commitments",
        json={
            "plan_date": START.isoformat(),
            "title": "Standup",
            "start_time": "10:00",
            "duration_minutes": 15,
            "repeat_interval": DAILY,
        },
    )
    assert created.status_code == 201
    series = created.json()
    assert series["repeat_interval"] == DAILY
    assert series["series_id"] is None

    later = (START + timedelta(days=9)).isoformat()
    plan = client.get("/api/focus", params={"date": later}).json()
    assert [(c["entry_id"], c["repeat_interval"]) for c in plan["commitments"]] == [
        (series["id"], DAILY)
    ]

    skipped = client.post(
        f"/api/focus/entries/{series['id']}/skip", json={"plan_date": later}
    )
    assert skipped.status_code == 200
    skip = skipped.json()
    assert (skip["series_id"], skip["plan_date"], skip["title"]) == (
        series["id"],
        later,
        None,
    )
    assert client.get("/api/focus", params={"date": later}).json()["commitments"] == []
    other_day = (START + timedelta(days=10)).isoformat()
    assert len(client.get("/api/focus", params={"date": other_day}).json()["commitments"]) == 1

    # Undo the skip.
    assert client.delete(f"/api/focus/entries/{skip['id']}").status_code == 200
    assert len(client.get("/api/focus", params={"date": later}).json()["commitments"]) == 1


def test_a_skipped_occurrence_is_listed_flagged_and_un_skips(client: TestClient) -> None:
    """#429: the plan lists a day's skipped occurrences apart, so the UI can take one back."""
    series = client.post(
        "/api/focus/commitments",
        json={
            "plan_date": START.isoformat(),
            "title": "Standup",
            "start_time": "10:00",
            "duration_minutes": 15,
            "repeat_interval": DAILY,
        },
    ).json()
    day = (START + timedelta(days=3)).isoformat()
    assert client.get("/api/focus", params={"date": day}).json()["skipped_commitments"] == []

    skip = client.post(
        f"/api/focus/entries/{series['id']}/skip", json={"plan_date": day}
    ).json()
    plan = client.get("/api/focus", params={"date": day, "start_time": "09:00"}).json()
    # Skipped: out of the live list and taking no time, but listed with its skip.
    assert plan["commitments"] == []
    assert plan["used_minutes"] == 0
    assert [
        (c["entry_id"], c["skip_entry_id"], c["title"], c["start_time"], c["end_time"])
        for c in plan["skipped_commitments"]
    ] == [(series["id"], skip["id"], "Standup", "10:00", "10:15")]
    assert plan["skipped_commitments"][0]["repeat_interval"] == DAILY
    # Only that day carries it.
    other = client.get("/api/focus", params={"date": START.isoformat()}).json()
    assert other["skipped_commitments"] == []
    # A window that starts after the occurrence ended drops it, as for a live one.
    late = client.get("/api/focus", params={"date": day, "start_time": "11:00"}).json()
    assert late["skipped_commitments"] == []

    # Un-skip through the existing write path: the occurrence is live again.
    assert client.delete(f"/api/focus/entries/{skip['id']}").status_code == 200
    plan = client.get("/api/focus", params={"date": day}).json()
    assert plan["skipped_commitments"] == []
    assert [c["entry_id"] for c in plan["commitments"]] == [series["id"]]
    # And its undo skips it again.
    assert client.post(f"/api/focus/entries/{skip['id']}/restore").status_code == 200
    plan = client.get("/api/focus", params={"date": day}).json()
    assert [c["skip_entry_id"] for c in plan["skipped_commitments"]] == [skip["id"]]


def test_a_removed_series_lists_no_skipped_occurrences(db_session: Session) -> None:
    series = _series(db_session, DAILY)
    focus_plan_service.skip_occurrence(db_session, series, START)
    focus_plan_service.remove_entry(db_session, series)
    db_session.commit()
    plan = focus_service.get_focus_plan(db_session, target_date=START, start_time="09:00")
    assert plan.skipped_commitments == []
    assert plan.commitments == []


def test_un_skip_is_audited(db_session: Session) -> None:
    series = _series(db_session, DAILY)
    skip = focus_plan_service.skip_occurrence(db_session, series, START)
    focus_plan_service.remove_entry(db_session, skip)
    focus_plan_service.restore_entry(db_session, skip)
    db_session.commit()
    events = db_session.execute(
        select(ActivityEvent.action, ActivityEvent.summary)
        .where(ActivityEvent.entity_type == "focus_entry", ActivityEvent.entity_id == skip.id)
        .order_by(ActivityEvent.id)
    ).all()
    assert [tuple(event) for event in events] == [
        ("skipped", 'Commitment "Standup" on 2026-06-20 skipped'),
        ("deleted", 'Commitment "Standup" on 2026-06-20 un-skipped'),
        ("restored", 'Commitment "Standup" on 2026-06-20 skipped again'),
    ]


def test_skip_route_refusals(client: TestClient) -> None:
    one_off = client.post(
        "/api/focus/commitments",
        json={
            "plan_date": START.isoformat(),
            "title": "Dentist",
            "start_time": "15:00",
            "duration_minutes": 60,
        },
    ).json()
    body = {"plan_date": START.isoformat()}
    assert client.post(f"/api/focus/entries/{one_off['id']}/skip", json=body).status_code == 409
    assert client.post("/api/focus/entries/9999/skip", json=body).status_code == 404


@pytest.mark.parametrize(
    "repeat",
    [
        {"unit": "year", "every": 1},
        {"unit": "day", "every": 0},
        {"unit": "day"},
        # A weekday set (#427) is weekly-only, non-empty, Mon 0..Sun 6, no repeats.
        {"unit": "day", "every": 1, "days": [0]},
        {"unit": "month", "every": 1, "days": [0]},
        {"unit": "week", "every": 1, "days": []},
        {"unit": "week", "every": 1, "days": [7]},
        {"unit": "week", "every": 1, "days": [-1]},
        {"unit": "week", "every": 1, "days": [1, 1]},
    ],
)
def test_a_bad_repeat_interval_is_422(client: TestClient, repeat: dict[str, Any]) -> None:
    response = client.post(
        "/api/focus/commitments",
        json={
            "plan_date": START.isoformat(),
            "title": "x",
            "start_time": "10:00",
            "duration_minutes": 30,
            "repeat_interval": repeat,
        },
    )
    assert response.status_code == 422
