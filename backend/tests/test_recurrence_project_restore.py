"""Restoring a project whose cascade tasks include a recurring occurrence (issue #157).

A series holds at most one active row per ``(recurrence_id, due_date)``. Project
restore used to reactivate its cascade-deleted tasks with the generic row
``restore()``, so a reoccupied slot surfaced as a raw IntegrityError that rolled
the whole restore back. It now raises ``OccurrenceConflictError`` before writing.
"""

from collections.abc import Sequence
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Project, Task
from app.services import projects as projects_service
from app.services import task_recurrence
from app.services import tasks as tasks_service
from app.tools import registry, runtime
from app.tools.registry import ToolError


def _live_series(db: Session, recurrence_id: str) -> Sequence[Task]:
    return (
        db.execute(
            select(Task)
            .where(Task.recurrence_id == recurrence_id, Task.deleted_at.is_(None))
            .order_by(Task.due_date)
        )
        .scalars()
        .all()
    )


def _conflicting_project_trash(db: Session) -> tuple[Project, str, int]:
    """Reproduce issue #157's setup.

    Returns ``(trashed_project_a, recurrence_id, deleted_occurrence_id)``: project A
    is in the trash holding a cascade-deleted 2026-07-08 occurrence, while a fresh
    live 2026-07-08 occurrence of the same series sits in project B.
    """
    project_a = projects_service.create_project(db, name="A")
    project_b = projects_service.create_project(db, name="B")
    first = tasks_service.create_task(
        db, project_id=project_b.id, title="water plants", due_date=date(2026, 7, 1)
    )
    tasks_service.update_task(
        db, first, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    recurrence_id = first.recurrence_id
    assert recurrence_id is not None

    tasks_service.mark_done(db, first)
    db.commit()
    successor = _live_series(db, recurrence_id)[-1]
    assert successor.due_date == date(2026, 7, 8)

    # Move the successor into A, then trash A so it cascade-deletes with it.
    tasks_service.update_task(db, successor, {"project_id": project_a.id})
    db.commit()
    successor_id = successor.id
    projects_service.soft_delete_project(db, project_a)
    db.commit()

    # With the successor gone, reconciling the done original spawns a *new* live
    # occurrence on the same 2026-07-08 date, back in B.
    task_recurrence.reconcile(db, [first.id])
    db.commit()
    live = _live_series(db, recurrence_id)
    assert [t.due_date for t in live] == [date(2026, 7, 1), date(2026, 7, 8)]
    assert live[-1].id != successor_id

    trashed = projects_service.get_deleted_project(db, project_a.id)
    assert trashed is not None
    return trashed, recurrence_id, successor_id


# --- Service ----------------------------------------------------------------


def test_restore_project_with_occupied_occurrence_raises_conflict(
    db_session: Session,
) -> None:
    project_a, recurrence_id, successor_id = _conflicting_project_trash(db_session)

    with pytest.raises(tasks_service.OccurrenceConflictError) as excinfo:
        projects_service.restore_project(db_session, project_a, restore_tasks=True)
    db_session.rollback()

    assert "2026-07-08" in str(excinfo.value)
    # Atomic: neither the project nor the task came back.
    assert projects_service.get_project(db_session, project_a.id) is None
    assert projects_service.get_deleted_project(db_session, project_a.id) is not None
    assert successor_id not in {t.id for t in _live_series(db_session, recurrence_id)}
    assert len(_live_series(db_session, recurrence_id)) == 2


def test_restore_project_without_tasks_still_works(db_session: Session) -> None:
    # The conflict is only about reactivating the occurrence; the escape hatch of
    # restoring the project alone stays open.
    project_a, recurrence_id, successor_id = _conflicting_project_trash(db_session)

    restored, count = projects_service.restore_project(
        db_session, project_a, restore_tasks=False
    )
    db_session.commit()

    assert count == 0
    assert projects_service.get_project(db_session, restored.id) is not None
    assert len(_live_series(db_session, recurrence_id)) == 2


def test_restore_project_after_clearing_the_conflict_works(
    db_session: Session,
) -> None:
    project_a, recurrence_id, successor_id = _conflicting_project_trash(db_session)
    blocker = _live_series(db_session, recurrence_id)[-1]
    tasks_service.soft_delete_task(db_session, blocker)
    db_session.commit()

    _restored, count = projects_service.restore_project(
        db_session, project_a, restore_tasks=True
    )
    db_session.commit()

    assert count == 1
    assert successor_id in {t.id for t in _live_series(db_session, recurrence_id)}


# --- Route ------------------------------------------------------------------


def test_restore_project_route_returns_actionable_409(
    client: TestClient, db_session: Session
) -> None:
    project_a, recurrence_id, _successor_id = _conflicting_project_trash(db_session)

    response = client.post(
        f"/api/projects/{project_a.id}/restore", params={"restore_tasks": True}
    )

    assert response.status_code == 409
    assert "2026-07-08" in response.json()["detail"]
    db_session.expire_all()
    assert projects_service.get_deleted_project(db_session, project_a.id) is not None


# --- Agent tool -------------------------------------------------------------


def test_tool_restore_project_raises_tool_error(
    db_session: Session,
    test_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_a, _recurrence_id, _successor_id = _conflicting_project_trash(db_session)
    project_id = project_a.id
    # StaticPool hands out one connection; release the test session's transaction.
    db_session.close()
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)

    with pytest.raises(ToolError) as excinfo:
        registry.call_tool(
            "restore_project",
            {"project_id": project_id, "restore_tasks": True},
            actor="agent",
        )

    assert "2026-07-08" in str(excinfo.value)
