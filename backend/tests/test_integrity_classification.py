"""SQLite uniqueness errors are classified by column tuple, not index name.

SQLite never reports the *name* of the partial unique index it rejected — it
reports the constrained columns — so these tests pin the real emitted messages
for both partial indexes and the domain translations that hang off them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency, TaskWorkflowStatus
from app.services import task_dependencies as deps_service
from app.services import task_recurrence as recurrence_service
from app.services import tasks as tasks_service
from app.services.integrity import violates_unique_columns


def _duplicate_occurrence_error(db: Session) -> IntegrityError:
    """Trip ``uq_tasks_active_occurrence`` for real and return the error."""
    for _ in range(2):
        db.add(
            Task(
                title="water the plants",
                recurrence_id="series-1",
                due_date=date(2026, 7, 25),
                workflow_status=TaskWorkflowStatus.open,
            )
        )
    with pytest.raises(IntegrityError) as caught:
        db.flush()
    db.rollback()
    return caught.value


def _duplicate_edge_error(db: Session) -> IntegrityError:
    """Trip ``uq_task_dependencies_active_edge`` for real and return the error."""
    first = tasks_service.create_task(db, project_id=None, title="deploy")
    second = tasks_service.create_task(db, project_id=None, title="build")
    db.flush()
    for _ in range(2):
        db.add(TaskDependency(task_id=first.id, depends_on_task_id=second.id))
    with pytest.raises(IntegrityError) as caught:
        db.flush()
    db.rollback()
    return caught.value


def test_sqlite_reports_columns_not_index_names(db_session: Session) -> None:
    # The bug: the old handlers searched for these index names, which never appear.
    occurrence = str(_duplicate_occurrence_error(db_session).orig)
    assert "UNIQUE constraint failed: tasks.recurrence_id, tasks.due_date" in occurrence
    assert "uq_tasks_active_occurrence" not in occurrence

    edge = str(_duplicate_edge_error(db_session).orig)
    assert (
        "UNIQUE constraint failed: task_dependencies.task_id, "
        "task_dependencies.depends_on_task_id" in edge
    )
    assert "uq_task_dependencies_active_edge" not in edge


def test_violates_unique_columns_matches_active_occurrence(
    db_session: Session,
) -> None:
    exc = _duplicate_occurrence_error(db_session)
    assert violates_unique_columns(exc, "tasks", ("recurrence_id", "due_date"))
    # Order-insensitive, but not column- or table-insensitive.
    assert violates_unique_columns(exc, "tasks", ("due_date", "recurrence_id"))
    assert not violates_unique_columns(exc, "tasks", ("recurrence_id",))
    assert not violates_unique_columns(exc, "tasks", ("recurrence_id", "title"))
    assert not violates_unique_columns(
        exc, "task_dependencies", ("recurrence_id", "due_date")
    )


def test_violates_unique_columns_matches_active_edge(db_session: Session) -> None:
    exc = _duplicate_edge_error(db_session)
    assert violates_unique_columns(
        exc, "task_dependencies", ("task_id", "depends_on_task_id")
    )
    assert not violates_unique_columns(exc, "task_dependencies", ("task_id",))


def test_non_uniqueness_integrity_errors_are_not_claimed(db_session: Session) -> None:
    """A foreign-key breach must not be mistaken for a uniqueness conflict."""
    db_session.add(TaskDependency(task_id=999_999, depends_on_task_id=888_888))
    with pytest.raises(IntegrityError) as caught:
        db_session.flush()
    db_session.rollback()
    assert not violates_unique_columns(
        caught.value, "task_dependencies", ("task_id", "depends_on_task_id")
    )


def test_create_next_occurrence_returns_the_winner_after_a_stale_read(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The insert loses the race; the handler must return the winning row."""
    parent = tasks_service.create_task(
        db_session,
        project_id=None,
        title="water the plants",
        due_date=date(2026, 7, 25),
    )
    tasks_service.update_task(
        db_session, parent, {"repeat_interval": {"unit": "day", "every": 1}}
    )
    db_session.flush()
    assert parent.recurrence_id is not None
    winner = recurrence_service.create_next_occurrence(db_session, parent)
    db_session.flush()

    # Simulate the stale guard a caller on a read session gets: the pre-insert
    # lookup misses the winner, so the insert hits the partial unique index.
    real = recurrence_service.find_live_occurrence_on
    calls = {"n": 0}

    def stale(*args: Any, **kwargs: Any) -> Task | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real(*args, **kwargs)

    monkeypatch.setattr(recurrence_service, "find_live_occurrence_on", stale)
    monkeypatch.setattr(
        recurrence_service, "_find_skipped_occurrence_on", lambda *a, **k: None
    )

    assert recurrence_service.create_next_occurrence(db_session, parent).id == winner.id


def test_add_dependency_after_a_stale_read_raises_duplicate(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tasks_service.create_task(db_session, project_id=None, title="deploy")
    second = tasks_service.create_task(db_session, project_id=None, title="build")
    db_session.flush()
    deps_service.add_dependency(db_session, first.id, second.id)
    db_session.flush()

    # The duplicate pre-check is a read and a read can be stale; the index is the
    # real gate, and its violation must still become a DuplicateDependencyError.
    monkeypatch.setattr(deps_service, "_depends_on_ids", lambda *a, **k: [])

    with pytest.raises(deps_service.DuplicateDependencyError):
        deps_service.add_dependency(db_session, first.id, second.id)
