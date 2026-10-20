"""The stored day plan (#319): pins, commitments, and the packer that honours them."""

from collections.abc import Generator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActivityEvent, FocusPlanEntry, TaskPriority, TaskWorkflowStatus
from app.services import focus as focus_service
from app.services import focus_plan as focus_plan_service
from app.services import task_dependencies as deps_service
from app.services import task_trash
from app.services import tasks as tasks_service
from app.tools import registry, runtime

TARGET = date(2026, 6, 20)


def _task(db: Session, title: str, **kwargs: Any) -> int:
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


def _commitment(db: Session, title: str, start: str, minutes: int) -> FocusPlanEntry:
    entry = focus_plan_service.add_commitment(
        db,
        plan_date=TARGET,
        title=title,
        start_minute=focus_plan_service.parse_time(start),
        duration_minutes=minutes,
    )
    db.commit()
    return entry


def _plan(db: Session, start: str = "09:00", minutes: int = 360) -> Any:
    return focus_service.get_focus_plan(
        db, target_date=TARGET, start_time=start, available_minutes=minutes
    )


def _times(plan: Any) -> list[tuple[int, str, str]]:
    return [(b.task_id, b.start_time, b.end_time) for b in plan.scheduled]


# --- Packing helpers ----------------------------------------------------------


def test_free_gaps_merge_overlapping_fixed_blocks() -> None:
    gaps = focus_service._free_gaps(540, 900, [(600, 660), (630, 700), (840, 960)])
    assert gaps == [[540, 600], [700, 840]]


def test_place_is_first_fit_and_shrinks_the_gap() -> None:
    gaps = [[540, 570], [600, 720]]
    assert focus_service._place(60, gaps) == 600
    assert focus_service._place(30, gaps) == 540
    assert focus_service._place(90, gaps) is None
    assert gaps == [[570, 570], [660, 720]]


# --- Packing with the overlay ---------------------------------------------------


def test_a_day_without_entries_packs_exactly_as_before(db_session: Session) -> None:
    a = _task(db_session, "a", priority=TaskPriority.urgent, estimated_minutes=30)
    b = _task(db_session, "b", priority=TaskPriority.high, estimated_minutes=45)

    plan = _plan(db_session)

    assert _times(plan) == [(a, "09:00", "09:30"), (b, "09:30", "10:15")]
    assert plan.commitments == []
    assert not any(block.pinned or block.fixed for block in plan.scheduled)


def test_a_commitment_splits_the_day_and_tasks_flow_around_it(
    db_session: Session,
) -> None:
    first = _task(db_session, "first", priority=TaskPriority.urgent, estimated_minutes=60)
    second = _task(db_session, "second", priority=TaskPriority.high, estimated_minutes=60)
    meeting = _commitment(db_session, "Standup", "10:30", 30)

    plan = _plan(db_session)

    # "second" doesn't fit the 30 minutes left before 10:30, so it jumps past.
    assert _times(plan) == [(first, "09:00", "10:00"), (second, "11:00", "12:00")]
    assert [(c.entry_id, c.title, c.start_time, c.end_time) for c in plan.commitments] == [
        (meeting.id, "Standup", "10:30", "11:00")
    ]
    assert plan.used_minutes == 150


def test_a_smaller_task_backfills_the_gap_before_a_commitment(
    db_session: Session,
) -> None:
    big = _task(db_session, "big", priority=TaskPriority.urgent, estimated_minutes=90)
    small = _task(db_session, "small", priority=TaskPriority.low, estimated_minutes=30)
    _commitment(db_session, "Standup", "10:00", 30)

    plan = _plan(db_session)

    # Rank order is big then small, but big can't fit before 10:00; small can.
    assert _times(plan) == [(small, "09:00", "09:30"), (big, "10:30", "12:00")]


def test_a_floating_pin_beats_higher_ranked_work_and_never_overflows(
    db_session: Session,
) -> None:
    urgent = _task(db_session, "urgent", priority=TaskPriority.urgent, estimated_minutes=60)
    low = _task(db_session, "low", priority=TaskPriority.low, estimated_minutes=60)
    pin = _pin(db_session, low)

    plan = _plan(db_session, minutes=60)

    assert [b.task_id for b in plan.scheduled] == [low]
    assert plan.scheduled[0].pinned and not plan.scheduled[0].fixed
    assert plan.scheduled[0].entry_id == pin.id
    assert [o.task_id for o in plan.overflow] == [urgent]


def test_a_floating_pin_that_fits_no_gap_runs_past_the_day(
    db_session: Session,
) -> None:
    filler = _task(db_session, "filler", estimated_minutes=60)
    _pin(db_session, filler)
    late = _task(db_session, "late", estimated_minutes=45)
    _pin(db_session, late)

    plan = _plan(db_session, minutes=60)

    assert _times(plan) == [(filler, "09:00", "10:00"), (late, "10:00", "10:45")]
    assert plan.overflow == []
    assert plan.used_minutes == 105  # past the 60-minute capacity, honestly


def test_a_timed_pin_is_a_fixed_block(db_session: Session) -> None:
    first = _task(db_session, "first", priority=TaskPriority.urgent, estimated_minutes=60)
    timed = _task(db_session, "timed", priority=TaskPriority.low, estimated_minutes=30)
    _pin(db_session, timed, "09:30")

    plan = _plan(db_session)

    assert _times(plan) == [(timed, "09:30", "10:00"), (first, "10:00", "11:00")]
    assert plan.scheduled[0].fixed


def test_repinning_moves_the_existing_pin(db_session: Session) -> None:
    task_id = _task(db_session, "move me", estimated_minutes=30)
    first = _pin(db_session, task_id, "10:00")
    again = _pin(db_session, task_id, "14:00")

    assert again.id == first.id
    assert _times(_plan(db_session)) == [(task_id, "14:00", "14:30")]


@pytest.mark.parametrize("state", ["done", "deferred", "blocked", "trashed"])
def test_a_pin_whose_task_left_the_day_is_ignored(
    db_session: Session, state: str
) -> None:
    task_id = _task(db_session, "pinned", estimated_minutes=30)
    entry = _pin(db_session, task_id, "10:00")
    task = tasks_service.get_task(db_session, task_id)
    assert task is not None
    if state == "done":
        tasks_service.mark_done(db_session, task)
    elif state == "deferred":
        tasks_service.update_task(
            db_session, task, {"deferred_until": TARGET + timedelta(days=1)}
        )
    elif state == "blocked":
        blocker = _task(db_session, "blocker")
        deps_service.add_dependency(db_session, task_id, blocker)
    else:
        tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    plan = _plan(db_session)

    assert task_id not in [b.task_id for b in plan.scheduled]
    if state == "blocked":
        assert task_id in [b.task_id for b in plan.blocked]
    # Ignored, not deleted: the decision is still on record.
    db_session.refresh(entry)
    assert entry.deleted_at is None


def test_a_past_commitment_is_dropped(db_session: Session) -> None:
    _commitment(db_session, "Breakfast", "08:00", 30)
    assert _plan(db_session).commitments == []


def test_a_timed_pin_that_already_passed_floats(db_session: Session) -> None:
    task_id = _task(db_session, "missed", estimated_minutes=30)
    _pin(db_session, task_id, "08:00")

    plan = _plan(db_session)

    assert _times(plan) == [(task_id, "09:00", "09:30")]
    assert plan.scheduled[0].pinned and not plan.scheduled[0].fixed


def test_a_commitment_past_capacity_still_shows(db_session: Session) -> None:
    _commitment(db_session, "Late call", "17:00", 60)

    plan = _plan(db_session, minutes=60)

    assert [c.start_time for c in plan.commitments] == ["17:00"]
    assert plan.used_minutes == 60


# --- Service rules ------------------------------------------------------------


def test_writes_are_audited(db_session: Session) -> None:
    task_id = _task(db_session, "audited")
    entry = _pin(db_session, task_id)
    focus_plan_service.remove_entry(db_session, entry)
    focus_plan_service.restore_entry(db_session, entry)
    db_session.commit()

    actions = db_session.execute(
        select(ActivityEvent.action)
        .where(ActivityEvent.entity_type == "focus_entry")
        .order_by(ActivityEvent.id)
    ).scalars().all()
    assert actions == ["created", "deleted", "restored"]


def test_a_subtask_or_done_task_cannot_be_pinned(db_session: Session) -> None:
    parent = _task(db_session, "parent")
    child = _task(db_session, "child", parent_task_id=parent)
    done = _task(db_session, "done", workflow_status=TaskWorkflowStatus.done)

    for task_id in (child, done):
        task = tasks_service.get_task(db_session, task_id)
        assert task is not None
        with pytest.raises(focus_plan_service.FocusEntryError):
            focus_plan_service.pin_task(db_session, plan_date=TARGET, task=task)


def test_restoring_a_pin_refuses_when_the_task_was_pinned_again(
    db_session: Session,
) -> None:
    task_id = _task(db_session, "twice")
    old = _pin(db_session, task_id)
    focus_plan_service.remove_entry(db_session, old)
    db_session.commit()
    _pin(db_session, task_id)

    with pytest.raises(focus_plan_service.FocusEntryError):
        focus_plan_service.restore_entry(db_session, old)


def test_purging_a_task_takes_its_pins_with_it(db_session: Session) -> None:
    task_id = _task(db_session, "doomed")
    _pin(db_session, task_id)
    task = tasks_service.get_task(db_session, task_id)
    assert task is not None
    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()
    task_trash.purge_task(db_session, task)
    db_session.commit()

    assert db_session.execute(select(FocusPlanEntry)).scalars().all() == []


# --- Routes -------------------------------------------------------------------


def test_routes_pin_move_unpin_and_restore(
    client: TestClient, db_session: Session
) -> None:
    task_id = _task(db_session, "routed", estimated_minutes=30)

    created = client.post(
        "/api/focus/pins", json={"plan_date": TARGET.isoformat(), "task_id": task_id}
    )
    assert created.status_code == 201
    entry_id = created.json()["id"]

    moved = client.patch(f"/api/focus/entries/{entry_id}", json={"start_time": "13:00"})
    assert moved.status_code == 200
    assert moved.json()["start_time"] == "13:00"

    plan = client.get("/api/focus", params={"date": TARGET.isoformat()}).json()
    assert plan["scheduled"][0]["start_time"] == "13:00"
    assert plan["scheduled"][0]["fixed"] is True

    assert client.delete(f"/api/focus/entries/{entry_id}").status_code == 200
    assert client.delete(f"/api/focus/entries/{entry_id}").status_code == 404
    assert client.post(f"/api/focus/entries/{entry_id}/restore").status_code == 200


def test_routes_add_and_edit_a_commitment(client: TestClient) -> None:
    created = client.post(
        "/api/focus/commitments",
        json={
            "plan_date": TARGET.isoformat(),
            "title": "  Standup ",
            "start_time": "14:00",
            "duration_minutes": 30,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert (body["title"], body["start_time"], body["task_id"]) == ("Standup", "14:00", None)

    floated = client.patch(f"/api/focus/entries/{body['id']}", json={"start_time": None})
    assert floated.status_code == 409  # a commitment always keeps its time

    plan = client.get("/api/focus", params={"date": TARGET.isoformat()}).json()
    assert [c["title"] for c in plan["commitments"]] == ["Standup"]


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "x", "start_time": "25:00", "duration_minutes": 30},
        {"title": " ", "start_time": "10:00", "duration_minutes": 30},
        {"title": "x", "start_time": "10:00", "duration_minutes": 0},
        {"title": "x", "start_time": "10:00", "duration_minutes": 30, "task_id": 1},
    ],
)
def test_commitment_validation_422s(client: TestClient, payload: dict[str, Any]) -> None:
    response = client.post(
        "/api/focus/commitments", json={"plan_date": TARGET.isoformat(), **payload}
    )
    assert response.status_code == 422


def test_pin_route_rejects_a_missing_or_done_task(
    client: TestClient, db_session: Session
) -> None:
    done = _task(db_session, "done", workflow_status=TaskWorkflowStatus.done)
    base = {"plan_date": TARGET.isoformat()}
    assert client.post("/api/focus/pins", json={**base, "task_id": 9999}).status_code == 404
    assert client.post("/api/focus/pins", json={**base, "task_id": done}).status_code == 409


def test_a_pin_cannot_take_a_title(client: TestClient, db_session: Session) -> None:
    task_id = _task(db_session, "plain")
    entry_id = client.post(
        "/api/focus/pins", json={"plan_date": TARGET.isoformat(), "task_id": task_id}
    ).json()["id"]
    response = client.patch(f"/api/focus/entries/{entry_id}", json={"title": "nope"})
    assert response.status_code == 409


# --- Agent tools ----------------------------------------------------------------


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def test_agent_tools_pin_commit_remove_and_restore(
    tool_db: sessionmaker[Session],
) -> None:
    # One shared in-memory connection: set up and inspect through short-lived
    # sessions so none holds a transaction open while a tool call begins its own.
    with tool_db() as setup:
        task_id = _task(setup, "agent pinned", estimated_minutes=30)
    day = TARGET.isoformat()

    pinned = registry.call_tool(
        "pin_focus_task",
        {"task_id": task_id, "date": day, "start_time": "11:00"},
        actor="agent:test",
    )
    meeting = registry.call_tool(
        "add_focus_commitment",
        {"title": "Sync", "start_time": "09:00", "duration_minutes": 60, "date": day},
        actor="agent:test",
    )
    plan = registry.call_tool("get_focus_plan", {"date": day}, actor="agent:test")
    assert [c.entry_id for c in plan.commitments] == [meeting.id]
    assert [(b.task_id, b.start_time) for b in plan.scheduled] == [(task_id, "11:00")]

    registry.call_tool("remove_focus_entry", {"entry_id": pinned.id}, actor="agent:test")
    restored = registry.call_tool(
        "restore_focus_entry", {"entry_id": pinned.id}, actor="agent:test"
    )
    assert restored.deleted_at is None

    with tool_db() as check:
        actors = check.execute(
            select(ActivityEvent.actor).where(ActivityEvent.entity_type == "focus_entry")
        ).scalars().all()
    assert set(actors) == {"agent:test"}
