"""End a commitment series from a day on, and edit one occurrence (#428)."""

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, FocusPlanEntry
from app.services import focus as focus_service
from app.services import focus_plan as focus_plan_service

START = date(2026, 6, 20)
DAILY = {"unit": "day", "every": 1}


def _series(
    db: Session,
    interval: dict[str, Any] | None = DAILY,
    *,
    title: str = "Standup",
    at: str = "10:00",
    minutes: int = 15,
) -> FocusPlanEntry:
    entry = focus_plan_service.add_commitment(
        db,
        plan_date=START,
        title=title,
        start_minute=focus_plan_service.parse_time(at),
        duration_minutes=minutes,
        repeat_interval=interval,
    )
    db.commit()
    return entry


def _day(n: int) -> date:
    return START + timedelta(days=n)


def _commitments(db: Session, day: date) -> list[tuple[str, str, int]]:
    plan = focus_service.get_focus_plan(db, target_date=day, start_time="09:00")
    return [(c.title, c.start_time, c.duration_minutes) for c in plan.commitments]


def _events(db: Session, entity_id: int) -> list[tuple[str, str]]:
    rows = db.execute(
        select(ActivityEvent.action, ActivityEvent.summary)
        .where(ActivityEvent.entity_type == "focus_entry", ActivityEvent.entity_id == entity_id)
        .order_by(ActivityEvent.id)
    ).all()
    return [(row[0], row[1]) for row in rows]


# --- Ending a series from a day on ---------------------------------------------


def test_stopping_a_series_keeps_the_days_before_it(db_session: Session) -> None:
    series = _series(db_session)
    focus_plan_service.stop_series(db_session, series, _day(3))
    db_session.commit()

    assert _commitments(db_session, START) == [("Standup", "10:00", 15)]
    assert _commitments(db_session, _day(2)) == [("Standup", "10:00", 15)]
    assert _commitments(db_session, _day(3)) == []
    assert _commitments(db_session, _day(30)) == []
    # Nothing was deleted: the series row is live, only its end moved.
    assert series.deleted_at is None
    plan = focus_service.get_focus_plan(db_session, target_date=_day(1), start_time="09:00")
    assert plan.commitments[0].series_start_date == START
    assert plan.commitments[0].stop_date == _day(3)


def test_lifting_the_end_is_the_undo_and_both_are_audited(db_session: Session) -> None:
    series = _series(db_session)
    focus_plan_service.stop_series(db_session, series, _day(5))
    focus_plan_service.stop_series(db_session, series, _day(3))
    # Undo of the second stop puts the first one back.
    focus_plan_service.stop_series(db_session, series, _day(5))
    focus_plan_service.stop_series(db_session, series, None)
    # Idempotent: no change, no event.
    focus_plan_service.stop_series(db_session, series, None)
    db_session.commit()

    assert _commitments(db_session, _day(10)) == [("Standup", "10:00", 15)]
    label = 'Commitment "Standup" at 10:00, daily from 2026-06-20,'
    assert _events(db_session, series.id)[1:] == [
        ("stopped", f"{label} stopped from 2026-06-25"),
        ("stopped", f"{label} stopped from 2026-06-23 (was stopped from 2026-06-25)"),
        ("stopped", f"{label} stopped from 2026-06-25 (was stopped from 2026-06-23)"),
        ("resumed", f"{label} repeats again with no end (was stopped from 2026-06-25)"),
    ]


def test_stop_refusals(db_session: Session) -> None:
    series = _series(db_session)
    one_off = _series(db_session, None, title="Dentist")
    with pytest.raises(focus_plan_service.FocusEntryError, match="remove the series"):
        focus_plan_service.stop_series(db_session, series, START)
    with pytest.raises(focus_plan_service.FocusEntryError, match="remove the series"):
        focus_plan_service.stop_series(db_session, series, START - timedelta(days=1))
    with pytest.raises(focus_plan_service.FocusEntryError, match="Only a repeating"):
        focus_plan_service.stop_series(db_session, one_off, _day(1))
    skip = focus_plan_service.skip_occurrence(db_session, series, _day(1))
    with pytest.raises(focus_plan_service.FocusEntryError, match="Only a repeating"):
        focus_plan_service.stop_series(db_session, skip, _day(2))


def test_the_check_constraint_keeps_stop_date_on_a_series_after_its_start(
    db_session: Session,
) -> None:
    one_off = _series(db_session, None)
    one_off.stop_date = _day(2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    series = _series(db_session)
    series.stop_date = START
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_ending_on_a_day_with_a_skip_or_an_edit_leaves_them_inert(
    db_session: Session,
) -> None:
    series = _series(db_session)
    skip = focus_plan_service.skip_occurrence(db_session, series, _day(4))
    override = focus_plan_service.edit_occurrence(
        db_session, series, _day(5), {"start_time": "11:00"}
    )
    before = focus_plan_service.edit_occurrence(
        db_session, series, _day(1), {"start_time": "12:00"}
    )
    focus_plan_service.stop_series(db_session, series, _day(4))
    db_session.commit()

    # The edit before the end still applies; after it, nothing shows at all.
    assert _commitments(db_session, _day(1)) == [("Standup", "12:00", 15)]
    for day in (_day(4), _day(5)):
        plan = focus_service.get_focus_plan(db_session, target_date=day, start_time="09:00")
        assert plan.commitments == [] and plan.skipped_commitments == []
    # Nor can a day past the end be skipped or edited.
    with pytest.raises(focus_plan_service.FocusEntryError, match="doesn't happen"):
        focus_plan_service.skip_occurrence(db_session, series, _day(6))
    with pytest.raises(focus_plan_service.FocusEntryError, match="doesn't happen"):
        focus_plan_service.edit_occurrence(db_session, series, _day(6), {"title": "x"})
    # The rows are kept, so lifting the end brings both days back as they were.
    assert skip.deleted_at is None and override.deleted_at is None and before.deleted_at is None
    focus_plan_service.stop_series(db_session, series, None)
    db_session.commit()
    plan = focus_service.get_focus_plan(db_session, target_date=_day(4), start_time="09:00")
    assert [c.skip_entry_id for c in plan.skipped_commitments] == [skip.id]
    assert _commitments(db_session, _day(5)) == [("Standup", "11:00", 15)]


def test_stop_routes(client: TestClient) -> None:
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
    url = f"/api/focus/entries/{series['id']}/stop"
    response = client.put(url, json={"stop_date": _day(2).isoformat()})
    assert response.status_code == 200
    assert response.json()["stop_date"] == _day(2).isoformat()
    plan = client.get("/api/focus", params={"date": _day(1).isoformat()}).json()
    assert plan["commitments"][0]["stop_date"] == _day(2).isoformat()
    assert plan["commitments"][0]["series_start_date"] == START.isoformat()
    assert client.get("/api/focus", params={"date": _day(2).isoformat()}).json()[
        "commitments"
    ] == []

    assert client.put(url, json={"stop_date": None}).json()["stop_date"] is None
    assert client.put(url, json={"stop_date": START.isoformat()}).status_code == 409
    assert client.put(url, json={}).status_code == 422
    assert client.put("/api/focus/entries/9999/stop", json={"stop_date": None}).status_code == 404


# --- Editing one occurrence -----------------------------------------------------


def test_editing_one_occurrence_leaves_the_other_days(db_session: Session) -> None:
    series = _series(db_session)
    override = focus_plan_service.edit_occurrence(
        db_session, series, _day(1), {"start_time": "11:00"}
    )
    db_session.commit()

    # Fields left out are copied from the series as it stands.
    assert (override.title, override.start_minute, override.duration_minutes) == (
        "Standup",
        660,
        15,
    )
    assert _commitments(db_session, _day(1)) == [("Standup", "11:00", 15)]
    assert _commitments(db_session, _day(2)) == [("Standup", "10:00", 15)]
    plan = focus_service.get_focus_plan(db_session, target_date=_day(1), start_time="09:00")
    block = plan.commitments[0]
    assert block.entry_id == series.id
    assert block.override_entry_id == override.id
    assert block.repeat_interval is not None
    # The edited block holds 11:00-11:15, so the free time packs around it there.
    assert plan.used_minutes == 15


def test_editing_an_edited_day_changes_its_edit(db_session: Session) -> None:
    series = _series(db_session)
    first = focus_plan_service.edit_occurrence(
        db_session, series, _day(1), {"start_time": "11:00"}
    )
    second = focus_plan_service.edit_occurrence(
        db_session, series, _day(1), {"title": "Standup + demo", "duration_minutes": 45}
    )
    db_session.commit()
    assert second.id == first.id
    assert _commitments(db_session, _day(1)) == [("Standup + demo", "11:00", 45)]


def test_a_series_edit_does_not_reach_an_edited_day(db_session: Session) -> None:
    series = _series(db_session)
    focus_plan_service.edit_occurrence(db_session, series, _day(1), {"start_time": "11:00"})
    focus_plan_service.update_entry(db_session, series, {"start_time": "09:30"})
    db_session.commit()
    assert _commitments(db_session, _day(1)) == [("Standup", "11:00", 15)]
    assert _commitments(db_session, _day(2)) == [("Standup", "09:30", 15)]


def test_resetting_an_edit_and_its_undo(db_session: Session) -> None:
    series = _series(db_session)
    override = focus_plan_service.edit_occurrence(
        db_session, series, _day(1), {"start_time": "11:00"}
    )
    focus_plan_service.remove_entry(db_session, override)
    db_session.commit()
    assert _commitments(db_session, _day(1)) == [("Standup", "10:00", 15)]

    focus_plan_service.restore_entry(db_session, override)
    db_session.commit()
    assert _commitments(db_session, _day(1)) == [("Standup", "11:00", 15)]

    day = "2026-06-21"
    label = f'Commitment "Standup" on {day}, as "Standup" at 11:00 for 15 min,'
    assert _events(db_session, override.id) == [
        ("overridden", f"{label} changed for that day only"),
        ("deleted", f"{label} reset to the series"),
        ("restored", f"{label} changed for that day again"),
    ]


def test_a_day_is_either_skipped_or_edited(db_session: Session) -> None:
    series = _series(db_session)
    override = focus_plan_service.edit_occurrence(
        db_session, series, _day(1), {"start_time": "11:00"}
    )
    with pytest.raises(focus_plan_service.FocusEntryError, match="reset it before skipping"):
        focus_plan_service.skip_occurrence(db_session, series, _day(1))
    skip = focus_plan_service.skip_occurrence(db_session, series, _day(2))
    with pytest.raises(focus_plan_service.FocusEntryError, match="un-skip it before editing"):
        focus_plan_service.edit_occurrence(db_session, series, _day(2), {"title": "x"})
    # A removed edit can't come back over a skip taken since, nor vice versa.
    focus_plan_service.remove_entry(db_session, override)
    focus_plan_service.skip_occurrence(db_session, series, _day(1))
    with pytest.raises(focus_plan_service.FocusEntryError, match="already skipped or edited"):
        focus_plan_service.restore_entry(db_session, override)
    focus_plan_service.remove_entry(db_session, skip)
    focus_plan_service.edit_occurrence(db_session, series, _day(2), {"title": "x"})
    with pytest.raises(focus_plan_service.FocusEntryError, match="already skipped or edited"):
        focus_plan_service.restore_entry(db_session, skip)


def test_edit_refusals(db_session: Session) -> None:
    series = _series(db_session, {"unit": "day", "every": 2})
    one_off = _series(db_session, None, title="Dentist")
    with pytest.raises(focus_plan_service.FocusEntryError, match="doesn't happen"):
        focus_plan_service.edit_occurrence(db_session, series, _day(1), {"title": "x"})
    with pytest.raises(focus_plan_service.FocusEntryError, match="doesn't happen"):
        focus_plan_service.edit_occurrence(
            db_session, series, START - timedelta(days=2), {"title": "x"}
        )
    with pytest.raises(focus_plan_service.FocusEntryError, match="Only a repeating"):
        focus_plan_service.edit_occurrence(db_session, one_off, START, {"title": "x"})
    with pytest.raises(focus_plan_service.FocusEntryError, match="Name what changes"):
        focus_plan_service.edit_occurrence(db_session, series, START, {})
    with pytest.raises(focus_plan_service.FocusEntryError, match="needs a start time"):
        focus_plan_service.edit_occurrence(db_session, series, START, {"title": None})
    override = focus_plan_service.edit_occurrence(db_session, series, START, {"title": "x"})
    # An edit is not a series: it can't be skipped, edited per-day or stopped.
    with pytest.raises(focus_plan_service.FocusEntryError, match="Only a repeating"):
        focus_plan_service.edit_occurrence(db_session, override, START, {"title": "y"})
    with pytest.raises(focus_plan_service.FocusEntryError, match="Only a repeating"):
        focus_plan_service.stop_series(db_session, override, _day(2))
    with pytest.raises(focus_plan_service.FocusEntryError, match="no order"):
        focus_plan_service.reorder_pins(db_session, plan_date=START, entry_ids=[override.id])


def test_removing_the_series_takes_its_edited_days_too(db_session: Session) -> None:
    series = _series(db_session)
    focus_plan_service.edit_occurrence(db_session, series, _day(1), {"start_time": "11:00"})
    focus_plan_service.remove_entry(db_session, series)
    db_session.commit()
    assert _commitments(db_session, _day(1)) == []
    focus_plan_service.restore_entry(db_session, series)
    db_session.commit()
    assert _commitments(db_session, _day(1)) == [("Standup", "11:00", 15)]


def test_occurrence_routes(client: TestClient) -> None:
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
    day = _day(1).isoformat()
    url = f"/api/focus/entries/{series['id']}/occurrence"
    response = client.post(url, json={"plan_date": day, "start_time": "11:00"})
    assert response.status_code == 200
    override = response.json()
    assert override["series_id"] == series["id"]
    assert (override["title"], override["start_time"], override["duration_minutes"]) == (
        "Standup",
        "11:00",
        15,
    )
    plan = client.get("/api/focus", params={"date": day}).json()
    assert [(c["entry_id"], c["override_entry_id"], c["start_time"]) for c in plan["commitments"]] == [
        (series["id"], override["id"], "11:00")
    ]
    # The override edits like any entry, for its day only.
    patched = client.patch(
        f"/api/focus/entries/{override['id']}", json={"duration_minutes": 30}
    )
    assert patched.status_code == 200 and patched.json()["duration_minutes"] == 30
    # Reset to the series, and back.
    assert client.delete(f"/api/focus/entries/{override['id']}").status_code == 200
    plan = client.get("/api/focus", params={"date": day}).json()
    assert [(c["override_entry_id"], c["start_time"]) for c in plan["commitments"]] == [
        (None, "10:00")
    ]
    assert client.post(f"/api/focus/entries/{override['id']}/restore").status_code == 200

    assert client.post(url, json={"plan_date": day, "start_time": "25:00"}).status_code == 422
    assert client.post(url, json={"plan_date": day, "title": " "}).status_code == 422
    assert client.post(url, json={"plan_date": day}).status_code == 409
    assert (
        client.post("/api/focus/entries/9999/occurrence", json={"plan_date": day}).status_code
        == 404
    )
