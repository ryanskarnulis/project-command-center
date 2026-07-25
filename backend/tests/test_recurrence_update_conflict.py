"""Editing a recurring occurrence onto a live sibling's due date (issue #158).

A series holds at most one active row per ``(recurrence_id, due_date)``. Update
used to apply the new date and let the partial unique index raise a raw
IntegrityError; it now rejects the edit with ``OccurrenceConflictError``.
"""

from collections.abc import Sequence
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Task
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.tools import registry, runtime
from app.tools.registry import ToolError


def _series(db: Session, recurrence_id: str) -> Sequence[Task]:
    return (
        db.execute(
            select(Task)
            .where(Task.recurrence_id == recurrence_id, Task.deleted_at.is_(None))
            .order_by(Task.due_date)
        )
        .scalars()
        .all()
    )


def _two_live_occurrences(db: Session) -> tuple[str, Task, Task]:
    """A weekly series with two live open occurrences: 2026-07-01 and 2026-07-08."""
    project = projects_service.create_project(db, name="Recurring")
    first = tasks_service.create_task(
        db, project_id=project.id, title="water plants", due_date=date(2026, 7, 1)
    )
    tasks_service.update_task(
        db, first, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    recurrence_id = first.recurrence_id
    assert recurrence_id is not None
    tasks_service.mark_done(db, first)
    db.commit()
    second = _series(db, recurrence_id)[-1]
    tasks_service.reopen_task(db, first)
    db.commit()
    assert second.due_date == date(2026, 7, 8)
    return recurrence_id, first, second


# --- Service ----------------------------------------------------------------


def test_update_onto_live_sibling_date_raises_conflict(db_session: Session) -> None:
    recurrence_id, first, second = _two_live_occurrences(db_session)

    with pytest.raises(tasks_service.OccurrenceConflictError) as excinfo:
        tasks_service.update_task(db_session, first, {"due_date": date(2026, 7, 8)})
    db_session.rollback()

    assert "2026-07-08" in str(excinfo.value)
    # Rejected edit leaves the series exactly as it was.
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 7, 1),
        date(2026, 7, 8),
    ]
    assert first.due_date == date(2026, 7, 1)
    assert second.due_date == date(2026, 7, 8)


def test_update_later_occurrence_onto_earlier_sibling_date_raises_conflict(
    db_session: Session,
) -> None:
    # The other direction: moving the spawned occurrence back onto the original.
    recurrence_id, _first, second = _two_live_occurrences(db_session)

    with pytest.raises(tasks_service.OccurrenceConflictError):
        tasks_service.update_task(db_session, second, {"due_date": date(2026, 7, 1)})
    db_session.rollback()

    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 7, 1),
        date(2026, 7, 8),
    ]


def test_update_onto_a_free_date_still_works(db_session: Session) -> None:
    recurrence_id, first, _second = _two_live_occurrences(db_session)

    tasks_service.update_task(db_session, first, {"due_date": date(2026, 7, 2)})
    db_session.commit()

    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 7, 2),
        date(2026, 7, 8),
    ]


def test_update_onto_a_trashed_sibling_date_still_works(db_session: Session) -> None:
    # Only *live* siblings hold a slot; a trashed row does not block the move.
    recurrence_id, first, second = _two_live_occurrences(db_session)
    tasks_service.soft_delete_task(db_session, second)
    db_session.commit()

    tasks_service.update_task(db_session, first, {"due_date": date(2026, 7, 8)})
    db_session.commit()

    assert [t.id for t in _series(db_session, recurrence_id)] == [first.id]


# --- Route ------------------------------------------------------------------


def test_patch_task_onto_live_sibling_date_returns_409(
    client: TestClient, db_session: Session
) -> None:
    _recurrence_id, first, _second = _two_live_occurrences(db_session)

    response = client.patch(
        f"/api/tasks/{first.id}", json={"due_date": "2026-07-08"}
    )

    assert response.status_code == 409
    assert "2026-07-08" in response.json()["detail"]
    db_session.expire_all()
    assert client.get(f"/api/tasks/{first.id}").json()["due_date"] == "2026-07-01"


# --- Agent tool -------------------------------------------------------------


def test_tool_update_task_onto_live_sibling_date_raises_tool_error(
    db_session: Session,
    test_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recurrence_id, first, _second = _two_live_occurrences(db_session)
    first_id = first.id
    # The in-memory engine hands out one connection (StaticPool); release the
    # test session's transaction so the tool's own session can open one.
    db_session.close()
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)

    with pytest.raises(ToolError) as excinfo:
        registry.call_tool(
            "update_task",
            {"task_id": first_id, "changes": {"due_date": "2026-07-08"}},
            actor="agent",
        )

    assert "2026-07-08" in str(excinfo.value)
