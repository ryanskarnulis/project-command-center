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


# --- Routes -----------------------------------------------------------------------


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
    [{"unit": "year", "every": 1}, {"unit": "day", "every": 0}, {"unit": "day"}],
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
