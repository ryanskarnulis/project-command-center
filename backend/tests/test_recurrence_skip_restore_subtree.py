"""Restoring a skipped checklist keeps its subtasks — issue #241.

``skip_occurrence`` used to soft-delete each direct child through its own
``soft_delete_task`` call, which made every child its own *delete root*: the
child got ``deleted_with_task_id = NULL`` and its own descendants were stamped
with the child's id instead of the occurrence's. With a live successor the
un-skip rewind hides that (the successor already carries fresh clones), but once
the successor is trashed or purged the restore falls through to the plain path
and the checklist came back as a bare leaf, its subtasks stranded in the trash.

The skip is now one cascade stamped with the occurrence's id, and restoring a
skipped occurrence with no live successor brings that cascade back with it —
without disturbing descendants the user had already trashed on their own.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task
from app.services import projects as projects_service
from app.services import task_recurrence, task_trash
from app.services import tasks as tasks_service


def _active_children(db: Session, parent_id: int) -> Sequence[Task]:
    return (
        db.execute(
            select(Task)
            .where(Task.parent_task_id == parent_id, Task.deleted_at.is_(None))
            .order_by(Task.id)
        )
        .scalars()
        .all()
    )


def _active_titled(db: Session, title: str) -> Sequence[Task]:
    return (
        db.execute(
            select(Task).where(Task.title == title, Task.deleted_at.is_(None))
        )
        .scalars()
        .all()
    )


def _build_checklist(db: Session) -> tuple[Task, Task, Task, Task]:
    """A weekly recurring checklist with a child, a grandchild, and a trashed step."""
    project = projects_service.create_project(db, name="Release")
    root = tasks_service.create_task(
        db,
        project_id=project.id,
        title="weekly release",
        due_date=date(2026, 8, 5),
    )
    db.commit()
    tasks_service.update_task(
        db, root, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    child = tasks_service.create_task(
        db, project_id=project.id, parent_task_id=root.id, title="Step"
    )
    grandchild = tasks_service.create_task(
        db, project_id=project.id, parent_task_id=child.id, title="Substep"
    )
    retired = tasks_service.create_task(
        db, project_id=project.id, parent_task_id=root.id, title="Retired step"
    )
    db.commit()
    # Trashed on its own terms, *before* the skip: it must stay in the trash.
    tasks_service.soft_delete_task(db, retired)
    db.commit()
    return root, child, grandchild, retired


def test_skip_stamps_the_whole_subtree_with_the_occurrence_id(
    db_session: Session,
) -> None:
    root, child, grandchild, retired = _build_checklist(db_session)
    root_id = root.id

    task_recurrence.skip_occurrence(db_session, root)
    db_session.commit()

    db_session.refresh(child)
    db_session.refresh(grandchild)
    db_session.refresh(retired)
    # The cascade names the skipped occurrence at every depth...
    assert child.deleted_at is not None
    assert child.deleted_with_task_id == root_id
    assert grandchild.deleted_at is not None
    assert grandchild.deleted_with_task_id == root_id
    # ...and does not adopt the row that was already in the trash.
    assert retired.deleted_with_task_id is None


def test_restore_after_the_successor_is_trashed_brings_the_checklist_back(
    db_session: Session,
) -> None:
    root, child, grandchild, retired = _build_checklist(db_session)
    root_id, child_id, grandchild_id, retired_id = (
        root.id,
        child.id,
        grandchild.id,
        retired.id,
    )
    recurrence_id = root.recurrence_id
    assert recurrence_id is not None

    successor = task_recurrence.skip_occurrence(db_session, root)
    db_session.commit()
    assert successor.due_date == date(2026, 8, 12)

    # The user changes their mind only after binning the generated occurrence, so
    # there is nothing left to rewind and the restore takes the plain path.
    tasks_service.soft_delete_task(db_session, successor)
    db_session.commit()

    skipped = task_trash.get_deleted_task(db_session, root_id)
    assert skipped is not None
    restored = task_trash.restore_task(db_session, skipped)
    db_session.commit()

    assert restored.id == root_id
    assert restored.deleted_at is None
    assert restored.skipped_at is None
    # The checklist is whole again, two levels deep.
    assert [t.id for t in _active_children(db_session, root_id)] == [child_id]
    assert [t.id for t in _active_children(db_session, child_id)] == [grandchild_id]
    # The independently trashed step stays where the user put it.
    retired_row = task_trash.get_deleted_task(db_session, retired_id)
    assert retired_row is not None

    # Exactly one live occurrence, and exactly one live copy of each restored row:
    # the successor's clones went to trash with it and must not be revived.
    live_series = [
        t
        for t in task_recurrence.get_series(db_session, recurrence_id)
        if t.deleted_at is None
    ]
    assert [t.id for t in live_series] == [root_id]
    assert [t.id for t in _active_titled(db_session, "Step")] == [child_id]
    assert [t.id for t in _active_titled(db_session, "Substep")] == [grandchild_id]
    assert _active_titled(db_session, "Retired step") == []


def test_restore_subtree_reports_the_rows_the_skip_removed(
    db_session: Session,
) -> None:
    """The opt-in subtree restore agrees with the un-skip it now overlaps."""
    root, child, grandchild, _retired = _build_checklist(db_session)
    root_id, child_id, grandchild_id = root.id, child.id, grandchild.id

    successor = task_recurrence.skip_occurrence(db_session, root)
    db_session.commit()
    tasks_service.soft_delete_task(db_session, successor)
    db_session.commit()

    skipped = task_trash.get_deleted_task(db_session, root_id)
    assert skipped is not None
    restored, restored_count = task_trash.restore_task_subtree(db_session, skipped)
    db_session.commit()

    assert restored.id == root_id
    assert restored_count == 2
    assert [t.id for t in _active_children(db_session, root_id)] == [child_id]
    assert [t.id for t in _active_children(db_session, child_id)] == [grandchild_id]


def test_unskip_still_rewinds_a_live_successor(db_session: Session) -> None:
    """Guard on #212's path: with a live successor the rewind is unchanged."""
    root, child, _grandchild, _retired = _build_checklist(db_session)
    root_id, child_id = root.id, child.id
    recurrence_id = root.recurrence_id
    assert recurrence_id is not None

    task_recurrence.skip_occurrence(db_session, root)
    db_session.commit()

    skipped = task_trash.get_deleted_task(db_session, root_id)
    assert skipped is not None
    live = task_trash.restore_task(db_session, skipped)
    db_session.commit()

    # The successor is rewound onto the un-skipped date and the original tree is
    # purged — the restored root is the clone, not the row the user clicked.
    assert live.id != root_id
    assert live.due_date == date(2026, 8, 5)
    live_series = [
        t
        for t in task_recurrence.get_series(db_session, recurrence_id)
        if t.deleted_at is None
    ]
    assert [t.id for t in live_series] == [live.id]
    assert db_session.get(Task, child_id) is None
    assert [t.title for t in _active_titled(db_session, "Step")] == ["Step"]


def test_route_restore_of_a_skipped_checklist_returns_its_subtasks(
    client: TestClient, db_session: Session
) -> None:
    root, child, grandchild, retired = _build_checklist(db_session)
    root_id, child_id, grandchild_id, retired_id = (
        root.id,
        child.id,
        grandchild.id,
        retired.id,
    )

    skip = client.post(f"/api/tasks/{root_id}/skip")
    assert skip.status_code == 200
    successor_id = skip.json()["id"]

    assert client.delete(f"/api/tasks/{successor_id}").status_code == 204

    # Default restore (no restore_subtasks): an un-skip is a subtree operation.
    restore = client.post(f"/api/tasks/{root_id}/restore")
    assert restore.status_code == 200
    assert restore.json()["id"] == root_id

    subtasks = client.get(f"/api/tasks/{root_id}/subtasks")
    assert subtasks.status_code == 200
    assert [t["id"] for t in subtasks.json()] == [child_id]

    deeper = client.get(f"/api/tasks/{child_id}/subtasks")
    assert deeper.status_code == 200
    assert [t["id"] for t in deeper.json()] == [grandchild_id]

    # The pre-trashed step is still in the trash and nothing was duplicated.
    trash = client.get("/api/trash")
    assert trash.status_code == 200
    trashed_task_ids = {t["id"] for t in trash.json()["tasks"]}
    assert retired_id in trashed_task_ids
    assert root_id not in trashed_task_ids
    assert [t.id for t in _active_titled(db_session, "Step")] == [child_id]
