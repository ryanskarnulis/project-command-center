"""The Focus session log and running clock on the server (#374)."""

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, FocusSession
from app.schemas.focus import FocusClockState
from app.services import focus_sessions as service
from app.services import task_trash
from app.services import tasks as tasks_service

TARGET = date(2026, 6, 20)
NEXT_DAY = TARGET + timedelta(days=1)


def _task(db: Session, title: str, **kwargs: Any) -> int:
    kwargs.setdefault("estimated_minutes", 30)
    task = tasks_service.create_task(db, project_id=None, title=title, **kwargs)
    db.commit()
    return task.id


def _events(db: Session) -> list[ActivityEvent]:
    return list(
        db.execute(
            select(ActivityEvent)
            .where(ActivityEvent.entity_type == "focus_session")
            .order_by(ActivityEvent.id)
        ).scalars()
    )


def _rows(db: Session) -> list[FocusSession]:
    return list(db.execute(select(FocusSession).order_by(FocusSession.id)).scalars())


# --- Service ------------------------------------------------------------------


def test_clock_start_pause_resume_and_clear(db_session: Session) -> None:
    task_id = _task(db_session, "write")
    task = tasks_service.get_task(db_session, task_id)
    assert task is not None

    clock = service.set_clock(
        db_session, plan_date=TARGET, task=task, state=FocusClockState.running, elapsed_ms=0
    )
    db_session.commit()
    assert service.clock_state(clock) is FocusClockState.running

    paused = service.set_clock(
        db_session,
        plan_date=TARGET,
        task=task,
        state=FocusClockState.paused,
        elapsed_ms=5 * 60_000,
    )
    db_session.commit()
    assert paused.id == clock.id  # same block, same row
    assert service.clock_state(paused) is FocusClockState.paused
    assert service.elapsed_ms(paused) == 5 * 60_000

    resumed = service.set_clock(
        db_session,
        plan_date=TARGET,
        task=task,
        state=FocusClockState.running,
        elapsed_ms=5 * 60_000,
    )
    db_session.commit()
    assert service.elapsed_ms(resumed) >= 5 * 60_000

    cleared = service.clear_clock(db_session, task_id=task_id, plan_date=TARGET)
    db_session.commit()
    assert cleared is not None and cleared.deleted_at is not None
    assert service.get_clock(db_session) is None
    # Soft delete only: the row is still there.
    assert len(_rows(db_session)) == 1
    assert [e.action for e in _events(db_session)] == [
        "created",
        "updated",
        "updated",
        "deleted",
    ]


def test_starting_another_block_replaces_the_one_live_clock(db_session: Session) -> None:
    a = tasks_service.get_task(db_session, _task(db_session, "a"))
    b = tasks_service.get_task(db_session, _task(db_session, "b"))
    assert a is not None and b is not None

    first = service.set_clock(
        db_session, plan_date=TARGET, task=a, state=FocusClockState.running, elapsed_ms=0
    )
    second = service.set_clock(
        db_session, plan_date=TARGET, task=b, state=FocusClockState.running, elapsed_ms=0
    )
    db_session.commit()

    live = service.get_clock(db_session)
    assert live is not None and live.id == second.id
    assert first.deleted_at is not None


def test_clock_from_another_day_is_replaced_not_reused(db_session: Session) -> None:
    task = tasks_service.get_task(db_session, _task(db_session, "a"))
    assert task is not None
    yesterday = service.set_clock(
        db_session,
        plan_date=TARGET,
        task=task,
        state=FocusClockState.paused,
        elapsed_ms=60_000,
    )
    today = service.set_clock(
        db_session,
        plan_date=NEXT_DAY,
        task=task,
        state=FocusClockState.running,
        elapsed_ms=0,
    )
    db_session.commit()
    assert today.id != yesterday.id
    assert yesterday.deleted_at is not None
    assert service.elapsed_ms(today) < 60_000


def test_clear_with_a_filter_leaves_another_blocks_clock(db_session: Session) -> None:
    a_id = _task(db_session, "a")
    b = tasks_service.get_task(db_session, _task(db_session, "b"))
    assert b is not None
    service.set_clock(
        db_session, plan_date=TARGET, task=b, state=FocusClockState.running, elapsed_ms=0
    )
    db_session.commit()

    assert service.clear_clock(db_session, task_id=a_id, plan_date=TARGET) is None
    assert service.clear_clock(db_session, task_id=b.id, plan_date=NEXT_DAY) is None
    assert service.get_clock(db_session) is not None


def test_the_database_refuses_a_second_live_clock(db_session: Session) -> None:
    task_id = _task(db_session, "a")
    db_session.add(FocusSession(plan_date=TARGET, task_id=task_id))
    db_session.commit()
    db_session.add(FocusSession(plan_date=NEXT_DAY, task_id=task_id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_finishing_books_the_block_and_stops_its_clock(db_session: Session) -> None:
    task = tasks_service.get_task(db_session, _task(db_session, "write"))
    assert task is not None
    service.set_clock(
        db_session, plan_date=TARGET, task=task, state=FocusClockState.running, elapsed_ms=0
    )
    block = service.finish_block(
        db_session, plan_date=TARGET, task=task, minutes=42, planned_minutes=30
    )
    db_session.commit()

    assert service.get_clock(db_session) is None
    log = service.session_log(db_session, TARGET)
    assert [(b.task_id, b.title, b.minutes, b.planned_minutes) for b in log.finished] == [
        (task.id, "write", 42, 30)
    ]
    # Booking again re-books in place rather than adding a second receipt.
    again = service.finish_block(
        db_session, plan_date=TARGET, task=task, minutes=45, planned_minutes=30
    )
    db_session.commit()
    assert again.id == block.id
    assert [b.minutes for b in service.session_log(db_session, TARGET).finished] == [45]


def test_finishing_another_row_leaves_the_current_clock(db_session: Session) -> None:
    current = tasks_service.get_task(db_session, _task(db_session, "current"))
    later = tasks_service.get_task(db_session, _task(db_session, "later"))
    assert current is not None and later is not None
    service.set_clock(
        db_session, plan_date=TARGET, task=current, state=FocusClockState.running, elapsed_ms=0
    )
    service.finish_block(db_session, plan_date=TARGET, task=later, minutes=30, planned_minutes=30)
    db_session.commit()
    live = service.get_clock(db_session)
    assert live is not None and live.task_id == current.id


def test_the_log_is_per_day(db_session: Session) -> None:
    task = tasks_service.get_task(db_session, _task(db_session, "a"))
    assert task is not None
    service.finish_block(db_session, plan_date=TARGET, task=task, minutes=10, planned_minutes=30)
    service.finish_block(
        db_session, plan_date=NEXT_DAY, task=task, minutes=20, planned_minutes=30
    )
    db_session.commit()
    assert [b.minutes for b in service.session_log(db_session, TARGET).finished] == [10]
    assert [b.minutes for b in service.session_log(db_session, NEXT_DAY).finished] == [20]


def test_remove_takes_the_block_out_and_is_idempotent(db_session: Session) -> None:
    task = tasks_service.get_task(db_session, _task(db_session, "a"))
    assert task is not None
    service.finish_block(db_session, plan_date=TARGET, task=task, minutes=10, planned_minutes=30)
    db_session.commit()

    assert service.remove_block(db_session, plan_date=TARGET, task_id=task.id) is not None
    assert service.remove_block(db_session, plan_date=TARGET, task_id=task.id) is None
    db_session.commit()
    assert service.session_log(db_session, TARGET).finished == []
    # And it can be booked again after the undo.
    service.finish_block(db_session, plan_date=TARGET, task=task, minutes=12, planned_minutes=30)
    db_session.commit()
    assert [b.minutes for b in service.session_log(db_session, TARGET).finished] == [12]


def test_purging_the_task_takes_its_session_rows(db_session: Session) -> None:
    task = tasks_service.get_task(db_session, _task(db_session, "a"))
    assert task is not None
    service.finish_block(db_session, plan_date=TARGET, task=task, minutes=10, planned_minutes=30)
    db_session.commit()
    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()
    trashed = task_trash.get_deleted_task(db_session, task.id)
    assert trashed is not None
    task_trash.purge_task(db_session, trashed)
    db_session.commit()
    assert _rows(db_session) == []


# --- Routes -------------------------------------------------------------------


def test_routes_round_trip_the_clock_and_the_log(
    client: TestClient, db_session: Session
) -> None:
    task_id = _task(db_session, "write")
    assert client.get("/api/focus/clock").json() is None

    started = client.put(
        "/api/focus/clock",
        json={
            "plan_date": TARGET.isoformat(),
            "task_id": task_id,
            "state": "running",
            "elapsed_ms": 120_000,
        },
    )
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["state"] == "running" and body["task_id"] == task_id
    assert body["elapsed_ms"] >= 120_000
    assert client.get("/api/focus/clock").json()["id"] == body["id"]

    finished = client.put(
        f"/api/focus/sessions/{TARGET.isoformat()}/tasks/{task_id}",
        json={"minutes": 3, "planned_minutes": 30},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["title"] == "write"
    assert client.get("/api/focus/clock").json() is None

    log = client.get("/api/focus/sessions", params={"plan_date": TARGET.isoformat()})
    assert [b["minutes"] for b in log.json()["finished"]] == [3]

    removed = client.delete(f"/api/focus/sessions/{TARGET.isoformat()}/tasks/{task_id}")
    assert removed.status_code == 204
    log = client.get("/api/focus/sessions", params={"plan_date": TARGET.isoformat()})
    assert log.json()["finished"] == []

    restored = client.put(
        "/api/focus/clock",
        json={
            "plan_date": TARGET.isoformat(),
            "task_id": task_id,
            "state": "paused",
            "elapsed_ms": 180_000,
        },
    )
    assert restored.json()["elapsed_ms"] == 180_000
    other = client.delete(
        "/api/focus/clock", params={"task_id": task_id + 999, "plan_date": TARGET.isoformat()}
    )
    assert other.status_code == 204
    assert client.get("/api/focus/clock").json() is not None
    cleared = client.delete(
        "/api/focus/clock", params={"task_id": task_id, "plan_date": TARGET.isoformat()}
    )
    assert cleared.status_code == 204
    assert client.get("/api/focus/clock").json() is None


def test_routes_validate(client: TestClient, db_session: Session) -> None:
    task_id = _task(db_session, "a")
    missing = client.put(
        "/api/focus/clock",
        json={"plan_date": TARGET.isoformat(), "task_id": 99999, "state": "running", "elapsed_ms": 0},
    )
    assert missing.status_code == 404
    for bad in (
        {"plan_date": TARGET.isoformat(), "task_id": task_id, "state": "idle", "elapsed_ms": 0},
        {"plan_date": TARGET.isoformat(), "task_id": task_id, "state": "running", "elapsed_ms": -1},
        {"plan_date": TARGET.isoformat(), "task_id": task_id, "state": "running"},
        {
            "plan_date": TARGET.isoformat(),
            "task_id": task_id,
            "state": "running",
            "elapsed_ms": 0,
            "extra": 1,
        },
    ):
        assert client.put("/api/focus/clock", json=bad).status_code == 422, bad
    assert (
        client.put(
            f"/api/focus/sessions/{TARGET.isoformat()}/tasks/{task_id}",
            json={"minutes": -1, "planned_minutes": 30},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/focus/sessions/{TARGET.isoformat()}/tasks/99999",
            json={"minutes": 1, "planned_minutes": 30},
        ).status_code
        == 404
    )
    assert client.get("/api/focus/sessions").status_code == 422
