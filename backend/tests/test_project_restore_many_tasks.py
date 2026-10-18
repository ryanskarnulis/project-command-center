"""Projects with more than 10,000 archived tasks restore and undo cleanly (#314).

The project-restore Undo receipt used to list every cascade task id in fields
capped at ``MAX_PURGE_IDS`` (10,000) — a *request* cap borrowed from the purge
endpoint. The restore builds that receipt from the database, so a project with
10,001 archived tasks raised a ``ValidationError`` mid-restore: a 500 and a
rollback, the project stuck in the trash even for "restore without its tasks".

The receipt now names no tasks — just the project's ``restored`` event — and
undo recomputes both task sets from the events the restore logged. These tests
exercise both sides of the old cap, both restore modes, a working Undo, and an
Undo refused atomically (409, nothing written) when work landed in between.

The tasks are bulk-inserted for speed; everything after that goes through the
real delete / restore / undo routes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import ColumnElement, func, insert, select
from sqlalchemy.orm import Session

from app.db.models import Project, Task


def _project_with_tasks(client: TestClient, db_session: Session, count: int) -> int:
    pid: int = client.post("/api/projects", json={"name": "Huge"}).json()["id"]
    db_session.execute(
        insert(Task),
        [{"project_id": pid, "title": f"t{i}"} for i in range(count)],
    )
    db_session.commit()
    return pid


def _count(db_session: Session, *conditions: ColumnElement[bool]) -> int:
    db_session.expire_all()
    stmt = select(func.count()).select_from(Task).where(*conditions)
    return db_session.scalar(stmt) or 0


def _archived(db_session: Session, pid: int) -> int:
    return _count(
        db_session, Task.deleted_at.is_not(None), Task.deleted_with_project_id == pid
    )


def _active(db_session: Session, pid: int) -> int:
    return _count(db_session, Task.deleted_at.is_(None), Task.project_id == pid)


def _standalone_trashed(db_session: Session, pid: int) -> int:
    return _count(
        db_session,
        Task.project_id == pid,
        Task.deleted_at.is_not(None),
        Task.deleted_with_project_id.is_(None),
    )


def _project_deleted(db_session: Session, pid: int) -> bool:
    db_session.expire_all()
    project = db_session.get(Project, pid)
    assert project is not None
    return project.deleted_at is not None


@pytest.mark.parametrize("count", [10_000, 10_001])
@pytest.mark.parametrize("restore_tasks", [False, True])
def test_restore_and_undo_a_project_with_many_archived_tasks(
    client: TestClient, db_session: Session, count: int, restore_tasks: bool
) -> None:
    pid = _project_with_tasks(client, db_session, count)
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert _archived(db_session, pid) == count

    resp = client.post(
        f"/api/projects/{pid}/restore",
        params={"restore_tasks": str(restore_tasks).lower()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["restored_task_count"] == (count if restore_tasks else 0)
    # The receipt stays two ids however many tasks moved.
    assert set(body["undo"]) == {"project_id", "restore_event_id"}
    assert not _project_deleted(db_session, pid)
    assert _archived(db_session, pid) == 0
    if restore_tasks:
        assert _active(db_session, pid) == count
    else:
        assert _standalone_trashed(db_session, pid) == count

    # Work lands inside the Undo window: the undo is refused and writes nothing.
    fresh = client.post(f"/api/projects/{pid}/tasks", json={"title": "new work"})
    assert fresh.status_code == 201
    refused = client.post("/api/trash/projects/undo-restore", json=body["undo"])
    assert refused.status_code == 409, refused.text
    assert not _project_deleted(db_session, pid)
    assert _archived(db_session, pid) == 0
    assert _active(db_session, pid) == (count + 1 if restore_tasks else 1)

    # Trash the new work again and the restore is back to exactly what it did.
    assert client.delete(f"/api/tasks/{fresh.json()['id']}").status_code == 204
    undo = client.post("/api/trash/projects/undo-restore", json=body["undo"])
    assert undo.status_code == 204, undo.text
    assert _project_deleted(db_session, pid)
    # Every task the restore touched is archived under the project again; the
    # independently trashed one stays in the standalone trash.
    assert _archived(db_session, pid) == count
    assert _standalone_trashed(db_session, pid) == 1
    assert _active(db_session, pid) == 0
