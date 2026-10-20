"""Reordering a day's floating pins (#375): service, route and agent tool."""

from collections.abc import Generator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActivityEvent, FocusPlanEntry, TaskWorkflowStatus
from app.services import focus as focus_service
from app.services import focus_plan as focus_plan_service
from app.services import tasks as tasks_service
from app.tools import registry, runtime
from app.tools.registry import ToolError

TARGET = date(2026, 6, 20)


def _task(db: Session, title: str, **kwargs: Any) -> int:
    kwargs.setdefault("estimated_minutes", 30)
    task = tasks_service.create_task(db, project_id=None, title=title, **kwargs)
    db.commit()
    return task.id


def _pin(db: Session, task_id: int, start: str | None = None) -> FocusPlanEntry:
    task = tasks_service.get_task(db, task_id)
    assert task is not None
    entry = focus_plan_service.pin_task(
        db,
        plan_date=TARGET,
        task=task,
        start_minute=None if start is None else focus_plan_service.parse_time(start),
    )
    db.commit()
    return entry


def _scheduled(db: Session) -> list[int]:
    plan = focus_service.get_focus_plan(
        db, target_date=TARGET, start_time="09:00", available_minutes=360
    )
    return [block.task_id for block in plan.scheduled]


def _reorder(db: Session, *entry_ids: int) -> list[FocusPlanEntry]:
    pins = focus_plan_service.reorder_pins(db, plan_date=TARGET, entry_ids=entry_ids)
    db.commit()
    return pins


def _reorder_events(db: Session) -> list[ActivityEvent]:
    return list(
        db.execute(
            select(ActivityEvent).where(
                ActivityEvent.entity_type == "focus_entry",
                ActivityEvent.action == "reordered",
            )
        ).scalars()
    )


# --- Service ------------------------------------------------------------------


def test_reordering_changes_the_order_the_plan_places_pins(db_session: Session) -> None:
    a, b, c = (_task(db_session, name) for name in ("a", "b", "c"))
    pa, pb, pc = (_pin(db_session, task_id) for task_id in (a, b, c))
    assert _scheduled(db_session) == [a, b, c]

    pins = _reorder(db_session, pc.id, pa.id, pb.id)

    assert [pin.id for pin in pins] == [pc.id, pa.id, pb.id]
    assert [pin.position for pin in pins] == [0, 1, 2]
    assert _scheduled(db_session) == [c, a, b]
    events = _reorder_events(db_session)
    assert len(events) == 1
    assert events[0].summary == (
        'Focus pins on 2026-06-20 reordered: "c", "a", "b"'
    )


def test_pins_left_out_keep_their_place(db_session: Session) -> None:
    a, b, c, d = (_task(db_session, name) for name in ("a", "b", "c", "d"))
    pa, pb, pc, pd = (_pin(db_session, task_id) for task_id in (a, b, c, d))

    # Only b and d are named: they swap slots, a and c stay put.
    _reorder(db_session, pd.id, pb.id)

    assert _scheduled(db_session) == [a, d, c, b]


def test_an_ignored_pin_keeps_its_slot_when_the_visible_ones_move(
    db_session: Session,
) -> None:
    a, b, c = (_task(db_session, name) for name in ("a", "b", "c"))
    pa, pb, pc = (_pin(db_session, task_id) for task_id in (a, b, c))
    # b is deferred past the day: its pin is ignored, not deleted, so the
    # client never sees it and can't name it.
    task_b = tasks_service.get_task(db_session, b)
    assert task_b is not None
    tasks_service.update_task(
        db_session, task_b, {"deferred_until": TARGET + timedelta(days=1)}
    )
    db_session.commit()

    _reorder(db_session, pc.id, pa.id)

    order = [entry.id for entry in focus_plan_service.list_entries(db_session, TARGET)]
    assert order == [pc.id, pb.id, pa.id]


def test_an_unchanged_order_writes_nothing(db_session: Session) -> None:
    a, b = (_task(db_session, name) for name in ("a", "b"))
    pa, pb = (_pin(db_session, task_id) for task_id in (a, b))

    _reorder(db_session, pa.id, pb.id)

    assert _reorder_events(db_session) == []


def test_reordering_untangles_tied_positions(db_session: Session) -> None:
    a, b = (_task(db_session, name) for name in ("a", "b"))
    pa, pb = (_pin(db_session, task_id) for task_id in (a, b))
    pb.position = pa.position  # what a restore after a re-pin can leave
    db_session.commit()

    _reorder(db_session, pb.id, pa.id)

    assert _scheduled(db_session) == [b, a]


def test_a_new_pin_still_appends_after_a_reorder(db_session: Session) -> None:
    a, b, c = (_task(db_session, name) for name in ("a", "b", "c"))
    pa, pb = (_pin(db_session, task_id) for task_id in (a, b))
    _reorder(db_session, pb.id, pa.id)

    _pin(db_session, c)

    assert _scheduled(db_session) == [b, a, c]


def test_reorder_refuses_commitments_strangers_and_duplicates(
    db_session: Session,
) -> None:
    a = _task(db_session, "a")
    pa = _pin(db_session, a)
    meeting = focus_plan_service.add_commitment(
        db_session, plan_date=TARGET, title="Sync", start_minute=600, duration_minutes=30
    )
    other_day_task = _task(db_session, "elsewhere")
    task = tasks_service.get_task(db_session, other_day_task)
    assert task is not None
    elsewhere = focus_plan_service.pin_task(
        db_session, plan_date=TARGET + timedelta(days=1), task=task
    )
    removed_task = _task(db_session, "removed")
    removed = _pin(db_session, removed_task)
    focus_plan_service.remove_entry(db_session, removed)
    db_session.commit()

    for entry_ids, message in [
        ([meeting.id, pa.id], "has no order"),
        ([elsewhere.id], "not a live entry"),
        ([removed.id], "not a live entry"),
        ([9999], "not a live entry"),
        ([pa.id, pa.id], "only once"),
        ([], "at least one"),
    ]:
        with pytest.raises(focus_plan_service.FocusEntryError, match=message):
            focus_plan_service.reorder_pins(
                db_session, plan_date=TARGET, entry_ids=entry_ids
            )


def test_a_timed_pin_that_floated_can_be_reordered(db_session: Session) -> None:
    a, b = (_task(db_session, name) for name in ("a", "b"))
    pa = _pin(db_session, a)
    pb = _pin(db_session, b, start="07:00")  # ended before the 09:00 start: floats

    assert _scheduled(db_session) == [a, b]
    _reorder(db_session, pb.id, pa.id)
    assert _scheduled(db_session) == [b, a]


# --- Route --------------------------------------------------------------------


def test_route_reorders_and_returns_the_new_order(
    client: TestClient, db_session: Session
) -> None:
    a, b = (_task(db_session, name) for name in ("a", "b"))
    pa, pb = (_pin(db_session, task_id) for task_id in (a, b))

    response = client.put(
        "/api/focus/pins/order",
        json={"plan_date": TARGET.isoformat(), "entry_ids": [pb.id, pa.id]},
    )

    assert response.status_code == 200
    assert [entry["id"] for entry in response.json()] == [pb.id, pa.id]
    plan = client.get("/api/focus", params={"date": TARGET.isoformat()}).json()
    assert [block["task_id"] for block in plan["scheduled"]] == [b, a]
    assert [block["pin_order"] for block in plan["scheduled"]] == [0, 1]


def test_pin_order_follows_position_not_the_clock(db_session: Session) -> None:
    # A 60-minute pin first, a 30-minute one second, and a commitment leaving
    # only 30 minutes before it: first fit puts the second pin earlier.
    big = _task(db_session, "big", estimated_minutes=60)
    small = _task(db_session, "small")
    _pin(db_session, big)
    _pin(db_session, small)
    focus_plan_service.add_commitment(
        db_session, plan_date=TARGET, title="Sync", start_minute=570, duration_minutes=30
    )
    db_session.commit()

    plan = focus_service.get_focus_plan(
        db_session, target_date=TARGET, start_time="09:00", available_minutes=360
    )

    by_clock = [(block.task_id, block.pin_order) for block in plan.scheduled]
    assert by_clock == [(small, 1), (big, 0)]


def test_route_409s_a_commitment_and_422s_a_bad_body(
    client: TestClient, db_session: Session
) -> None:
    meeting = client.post(
        "/api/focus/commitments",
        json={
            "plan_date": TARGET.isoformat(),
            "title": "Sync",
            "start_time": "10:00",
            "duration_minutes": 30,
        },
    ).json()
    day = TARGET.isoformat()
    conflict = client.put(
        "/api/focus/pins/order", json={"plan_date": day, "entry_ids": [meeting["id"]]}
    )
    assert conflict.status_code == 409
    for body in [
        {"plan_date": day, "entry_ids": []},
        {"plan_date": day, "entry_ids": [0]},
        {"entry_ids": [1]},
        {"plan_date": day, "entry_ids": [1], "extra": True},
    ]:
        assert client.put("/api/focus/pins/order", json=body).status_code == 422


# --- Agent tool ---------------------------------------------------------------


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def test_agent_tool_reorders_pins_as_the_agent(tool_db: sessionmaker[Session]) -> None:
    with tool_db() as setup:
        a, b = (_task(setup, name) for name in ("a", "b"))
        pa, pb = (_pin(setup, task_id).id for task_id in (a, b))
        done = _task(setup, "done", workflow_status=TaskWorkflowStatus.done)
    day = TARGET.isoformat()

    pins = registry.call_tool(
        "reorder_focus_pins", {"entry_ids": [pb, pa], "date": day}, actor="agent:test"
    )
    assert [pin.id for pin in pins] == [pb, pa]

    plan = registry.call_tool("get_focus_plan", {"date": day}, actor="agent:test")
    assert [block.task_id for block in plan.scheduled] == [b, a]

    with pytest.raises(ToolError, match="not a live entry"):
        registry.call_tool(
            "reorder_focus_pins", {"entry_ids": [done], "date": day}, actor="agent:test"
        )
    with pytest.raises(ValidationError):
        registry.call_tool("reorder_focus_pins", {"entry_ids": []}, actor="agent:test")

    with tool_db() as check:
        events = _reorder_events(check)
    assert [event.actor for event in events] == ["agent:test"]
