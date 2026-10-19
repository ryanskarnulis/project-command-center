"""Trash retention (#321): trash older than the configured window is purged."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import ActivityEvent, Project, Task
from app.jobs import trash_retention
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.services import trash as trash_service
from app.services.common import soft_delete

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _trash_project(db: Session, name: str, days_ago: float, tasks: int = 0) -> tuple[int, list[int]]:
    project = projects_service.create_project(db, name=name)
    task_ids = [
        tasks_service.create_task(db, project_id=project.id, title=f"{name} {i}").id
        for i in range(tasks)
    ]
    projects_service.soft_delete_project(db, project)
    db.flush()
    project.deleted_at = _ago(days_ago)
    for task_id in task_ids:
        task = db.get(Task, task_id)
        assert task is not None and task.deleted_with_project_id == project.id
        task.deleted_at = _ago(days_ago)
    db.commit()
    return project.id, task_ids


def _trash_task(db: Session, title: str, days_ago: float, parent: int | None = None) -> int:
    task = tasks_service.create_task(db, project_id=None, title=title, parent_task_id=parent)
    tasks_service.soft_delete_task(db, task)
    db.flush()
    task.deleted_at = _ago(days_ago)
    db.commit()
    return task.id


def _purge(db: Session, days: int = 30) -> trash_service.PurgeCounts:
    counts = trash_service.purge_expired(db, now=NOW, retention_days=days)
    db.commit()
    return counts


@pytest.fixture
def retention_on_session(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Session:
    """Route the job's write session onto the test DB, with a 30-day window."""

    @contextmanager
    def fake_write_session() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(trash_retention, "write_session", fake_write_session)
    monkeypatch.setattr(get_settings(), "trash_retention_days", 30)
    return db_session


def test_expired_project_is_purged_with_its_archived_tasks(db_session: Session) -> None:
    pid, task_ids = _trash_project(db_session, "Old", days_ago=31, tasks=2)

    counts = _purge(db_session)

    assert counts == trash_service.PurgeCounts(projects=1, tasks=2)
    assert db_session.get(Project, pid) is None
    assert all(db_session.get(Task, tid) is None for tid in task_ids)


def test_unexpired_roots_survive(db_session: Session) -> None:
    pid, task_ids = _trash_project(db_session, "Recent", days_ago=10, tasks=1)
    tid = _trash_task(db_session, "Almost", days_ago=29.9)
    old = _trash_task(db_session, "Expired", days_ago=30.1)

    counts = _purge(db_session)

    assert counts == trash_service.PurgeCounts(projects=0, tasks=1)
    assert db_session.get(Project, pid) is not None
    assert db_session.get(Task, task_ids[0]) is not None
    assert db_session.get(Task, tid) is not None
    assert db_session.get(Task, old) is None


def test_active_rows_are_never_touched(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Live")
    task = tasks_service.create_task(db_session, project_id=project.id, title="Live task")
    # Old rows, but not in the trash: age alone means nothing.
    project.created_at = _ago(400)
    db_session.commit()

    assert _purge(db_session) == trash_service.PurgeCounts(projects=0, tasks=0)
    assert db_session.get(Task, task.id) is not None


def test_protected_project_is_never_purged(db_session: Session) -> None:
    general = projects_service.ensure_default_project(db_session)
    soft_delete(general)
    general.deleted_at = _ago(90)
    db_session.commit()

    assert _purge(db_session) == trash_service.PurgeCounts(projects=0, tasks=0)
    assert db_session.get(Project, general.id) is not None


def test_zero_disables_retention(db_session: Session) -> None:
    pid, _ = _trash_project(db_session, "Ancient", days_ago=3650)
    tid = _trash_task(db_session, "Ancient task", days_ago=3650)

    assert _purge(db_session, days=0) == trash_service.PurgeCounts(projects=0, tasks=0)
    assert db_session.get(Project, pid) is not None
    assert db_session.get(Task, tid) is not None


def test_the_roots_age_decides_for_its_subtree(db_session: Session) -> None:
    # An old root takes its subtree even though a child's own stamp is young.
    root = tasks_service.create_task(db_session, project_id=None, title="Root")
    child = tasks_service.create_task(
        db_session, project_id=None, title="Child", parent_task_id=root.id
    )
    tasks_service.soft_delete_task(db_session, root)
    db_session.flush()
    assert child.deleted_with_task_id == root.id
    root.deleted_at = _ago(40)
    child.deleted_at = _ago(1)
    # And a young root holds a cascaded child whose own stamp is old.
    young = tasks_service.create_task(db_session, project_id=None, title="Young root")
    held = tasks_service.create_task(
        db_session, project_id=None, title="Held", parent_task_id=young.id
    )
    tasks_service.soft_delete_task(db_session, young)
    db_session.flush()
    young.deleted_at = _ago(2)
    held.deleted_at = _ago(40)
    db_session.commit()
    root_id, child_id, young_id, held_id = root.id, child.id, young.id, held.id

    counts = _purge(db_session)

    assert counts == trash_service.PurgeCounts(projects=0, tasks=2)
    assert db_session.get(Task, root_id) is None
    assert db_session.get(Task, child_id) is None
    assert db_session.get(Task, young_id) is not None
    assert db_session.get(Task, held_id) is not None


def test_a_project_takes_every_trashed_task_it_owns_like_a_manual_purge(
    db_session: Session,
) -> None:
    # The project's age decides: a task trashed on its own after the project
    # still goes with it, exactly as Delete forever on the project row would.
    pid, _ = _trash_project(db_session, "Old", days_ago=40)
    task = tasks_service.create_task(db_session, project_id=None, title="Late")
    task.project_id = pid
    soft_delete(task)
    task.deleted_at = _ago(1)
    db_session.commit()
    task_id = task.id

    assert _purge(db_session) == trash_service.PurgeCounts(projects=1, tasks=1)
    assert db_session.get(Task, task_id) is None


def test_run_once_records_purges_under_the_retention_actor(
    retention_on_session: Session,
) -> None:
    db = retention_on_session
    pid, task_ids = _trash_project(db, "Old", days_ago=31, tasks=1)
    tid = _trash_task(db, "Old task", days_ago=45)

    counts = trash_retention.run_once(now=NOW)

    assert counts == trash_service.PurgeCounts(projects=1, tasks=2)
    events = db.execute(
        select(ActivityEvent).where(ActivityEvent.action == "purged")
    ).scalars().all()
    assert {(e.entity_type, e.entity_id) for e in events} == {
        ("project", pid),
        ("task", task_ids[0]),
        ("task", tid),
    }
    assert {e.actor for e in events} == {"retention"}


def test_run_once_does_nothing_when_disabled(
    retention_on_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "trash_retention_days", 0)
    tid = _trash_task(retention_on_session, "Ancient", days_ago=3650)

    assert trash_retention.run_once(now=NOW) == trash_service.PurgeCounts(
        projects=0, tasks=0
    )
    assert retention_on_session.get(Task, tid) is not None


def test_loop_survives_a_failing_pass() -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def run() -> None:
        calls.append("run")
        if len(calls) == 1:
            raise RuntimeError("database is locked")

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 4:  # first delay + three intervals
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            trash_retention.retention_loop(
                run, first_delay=5, interval=60, sleep=fake_sleep
            )
        )

    # The first pass raised; the loop logged it and kept going.
    assert calls == ["run", "run", "run"]
    assert sleeps == [5, 60, 60, 60]


def test_start_is_a_noop_when_disabled_and_stop_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        # conftest switches the loop off for every test.
        assert trash_retention.start() is None
        monkeypatch.setattr(trash_retention, "enabled", True)
        monkeypatch.setattr(get_settings(), "trash_retention_days", 0)
        assert trash_retention.start() is None
        monkeypatch.setattr(get_settings(), "trash_retention_days", 30)
        task = trash_retention.start()
        assert task is not None and not task.done()
        await trash_retention.stop(task)
        assert task.cancelled()

    asyncio.run(scenario())


def test_trash_count_exposes_the_retention_window(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "trash_retention_days", 30)
    assert client.get("/api/trash/count").json()["retention_days"] == 30
    monkeypatch.setattr(get_settings(), "trash_retention_days", 0)
    assert client.get("/api/trash/count").json()["retention_days"] == 0


def test_retention_setting_defaults_to_30_and_rejects_negative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env in play
    monkeypatch.delenv("TRASH_RETENTION_DAYS", raising=False)
    assert Settings().trash_retention_days == 30
    monkeypatch.setenv("TRASH_RETENTION_DAYS", "0")
    assert Settings().trash_retention_days == 0
    monkeypatch.setenv("TRASH_RETENTION_DAYS", "-1")
    with pytest.raises(ValidationError):
        Settings()
