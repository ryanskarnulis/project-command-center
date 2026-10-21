"""Un-skipping a recurring checklist — issues #212 and #457.

``skip_occurrence`` clones the subtree (children keep their own ``project_id``)
and soft-deletes the original. Un-skipping with a live successor rewinds that
successor onto the skipped date, which makes the original occurrence obsolete.

It used to *purge* the original (unscoped, so a child filed in another project
was not left detached in standalone trash, #212) — a hard delete reachable from
the agent and MCP (#457). Now the original rows stay soft-deleted and are marked
``superseded_at``: out of the Tasks trash (so #212's stranded duplicate still
can't happen), refused by every restore, and brought back as the skipped
occurrence only by the un-skip's own Undo.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import FocusPlanEntry, Task
from app.schemas.trash import TaskRestoreUndo
from app.services import focus_plan, task_recurrence, task_trash
from app.services import projects as projects_service
from app.services import tasks as tasks_service


def _skipped_cross_project_checklist(db: Session) -> tuple[int, int, int, str]:
    """A weekly checklist in A with a child filed in B, skipped once.

    Returns the original parent id, the original child id, project B's id and
    the series' ``recurrence_id``.
    """
    project_a = projects_service.create_project(db, name="A")
    project_b = projects_service.create_project(db, name="B")
    parent = tasks_service.create_task(
        db, project_id=project_a.id, title="weekly checklist", due_date=date(2026, 7, 1)
    )
    db.commit()
    tasks_service.update_task(
        db, parent, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    recurrence_id = parent.recurrence_id
    assert recurrence_id is not None
    child = tasks_service.create_task(
        db, project_id=project_b.id, parent_task_id=parent.id, title="cross-project step"
    )
    db.commit()
    parent_id, child_id = parent.id, child.id

    task_recurrence.skip_occurrence(db, parent)
    db.commit()
    return parent_id, child_id, project_b.id, recurrence_id


def _unskip(db: Session, task_id: int) -> tuple[Task, TaskRestoreUndo]:
    skipped = task_trash.get_deleted_task(db, task_id)
    assert skipped is not None
    live, undo = task_trash.restore_task_with_undo(db, skipped)
    db.commit()
    return live, undo


def test_unskip_supersedes_the_whole_original_occurrence_across_projects(
    db_session: Session,
) -> None:
    parent_id, child_id, project_b_id, recurrence_id = _skipped_cross_project_checklist(
        db_session
    )
    trash_before = task_trash.count_standalone_deleted_tasks(db_session)
    live, _ = _unskip(db_session, parent_id)

    # The series is rewound to the un-skipped date, with exactly one occurrence.
    series = task_recurrence.get_series(db_session, recurrence_id)
    assert [t.id for t in series] == [live.id]
    assert live.due_date == date(2026, 7, 1)
    assert live.id != parent_id

    # Exactly one live child: the clone, still filed in project B.
    live_children = (
        db_session.query(Task)
        .filter(Task.parent_task_id == live.id, Task.deleted_at.is_(None))
        .all()
    )
    assert len(live_children) == 1
    assert live_children[0].project_id == project_b_id
    assert live_children[0].id != child_id

    # Nothing was hard-deleted: both originals are still there, soft-deleted,
    # superseded, and still a parent and child (not detached).
    parent = db_session.get(Task, parent_id)
    child = db_session.get(Task, child_id)
    assert parent is not None and child is not None
    assert parent.deleted_at is not None and parent.superseded_at is not None
    assert child.deleted_at is not None and child.superseded_at is not None
    assert child.parent_task_id == parent_id
    # The date is no longer skipped: the live occurrence holds it.
    assert parent.skipped_at is None
    assert task_recurrence.find_skipped_occurrence_on(
        db_session, recurrence_id, date(2026, 7, 1)
    ) is None

    # And neither is stranded in the trash as a restorable duplicate (#212).
    trashed_ids = {t.id for t in task_trash.list_deleted_tasks(db_session)}
    assert parent_id not in trashed_ids
    assert child_id not in trashed_ids
    assert task_trash.count_standalone_deleted_tasks(db_session) == trash_before - 2


def test_unskip_keeps_the_original_rows_focus_entries(db_session: Session) -> None:
    """A trashed task keeps its Focus pins; a superseded one does too (no purge)."""
    project = projects_service.create_project(db_session, name="P")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="daily", due_date=date(2026, 7, 1)
    )
    db_session.commit()
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "day", "every": 1}}
    )
    entry = focus_plan.pin_task(db_session, plan_date=date(2026, 7, 1), task=task)
    db_session.commit()
    task_id, entry_id = task.id, entry.id
    task_recurrence.skip_occurrence(db_session, task)
    db_session.commit()

    _unskip(db_session, task_id)

    kept = db_session.get(FocusPlanEntry, entry_id)
    assert kept is not None and kept.task_id == task_id


@pytest.mark.parametrize("which", ["parent", "child"])
def test_a_superseded_row_refuses_restore(db_session: Session, which: str) -> None:
    parent_id, child_id, _, recurrence_id = _skipped_cross_project_checklist(
        db_session
    )
    live, _ = _unskip(db_session, parent_id)
    target_id = parent_id if which == "parent" else child_id

    # Even with the date freed up — the live occurrence trashed — a superseded
    # row stays retired: it is an obsolete copy, and reviving it must neither
    # duplicate the series nor run the un-skip rewind a second time.
    tasks_service.soft_delete_task(db_session, live)
    db_session.commit()

    for restore in (task_trash.restore_task, task_trash.restore_task_subtree):
        target = task_trash.get_deleted_task(db_session, target_id)
        assert target is not None
        with pytest.raises(task_trash.SupersededTaskError):
            restore(db_session, target)
        db_session.rollback()

    live_rows = (
        db_session.query(Task)
        .filter(Task.recurrence_id == recurrence_id, Task.deleted_at.is_(None))
        .all()
    )
    assert live_rows == []
    assert db_session.get(Task, target_id).superseded_at is not None  # type: ignore[union-attr]


def test_restoring_a_superseded_row_over_http_is_a_409(
    client: TestClient, db_session: Session
) -> None:
    parent_id, child_id, _, _ = _skipped_cross_project_checklist(db_session)
    _unskip(db_session, parent_id)

    for task_id in (parent_id, child_id):
        for url in (
            f"/api/tasks/{task_id}/restore",
            f"/api/tasks/{task_id}/restore?restore_subtasks=true",
            f"/api/trash/tasks/{task_id}/restore",
        ):
            resp = client.post(url)
            assert resp.status_code == 409, (url, resp.text)
            assert "already restored" in resp.json()["detail"]


def test_restore_subtasks_unskip_does_not_touch_the_superseded_checklist(
    db_session: Session,
) -> None:
    """The agent's ``restore_subtasks=True`` path: the rewind, then no revival."""
    parent_id, child_id, _, _ = _skipped_cross_project_checklist(db_session)
    skipped = task_trash.get_deleted_task(db_session, parent_id)
    assert skipped is not None

    live, restored_count = task_trash.restore_task_subtree(db_session, skipped)
    db_session.commit()

    assert live.id != parent_id
    assert restored_count == 0
    child = db_session.get(Task, child_id)
    assert child is not None
    assert child.deleted_at is not None and child.superseded_at is not None


def test_undo_unskip_turns_the_original_back_into_the_skipped_occurrence(
    db_session: Session,
) -> None:
    parent_id, child_id, _, recurrence_id = _skipped_cross_project_checklist(
        db_session
    )
    live, undo = _unskip(db_session, parent_id)
    assert undo.unskip is not None
    assert undo.unskip.superseded_task_id == parent_id
    live_id = live.id

    task_trash.undo_task_restore(db_session, undo)
    db_session.commit()

    # The live occurrence is back on its later date...
    live = db_session.get(Task, live_id)  # type: ignore[assignment]
    assert live is not None and live.due_date == date(2026, 7, 8)
    # ...and the original rows are the skipped occurrence again, in the trash.
    parent = db_session.get(Task, parent_id)
    child = db_session.get(Task, child_id)
    assert parent is not None and child is not None
    assert parent.superseded_at is None and child.superseded_at is None
    assert parent.deleted_at is not None and parent.skipped_at == parent.deleted_at
    assert child.deleted_with_task_id == parent_id
    skipped = task_recurrence.find_skipped_occurrence_on(
        db_session, recurrence_id, date(2026, 7, 1)
    )
    assert skipped is not None and skipped.id == parent_id
    trashed_ids = {t.id for t in task_trash.list_deleted_tasks(db_session)}
    assert {parent_id, child_id} <= trashed_ids
    # Nothing new was minted to stand in for it.
    assert [t.id for t in task_recurrence.get_series(db_session, recurrence_id)] == [
        parent_id,
        live_id,
    ]

    # And the un-skip is repeatable from there.
    again, _ = _unskip(db_session, parent_id)
    assert again.id == live_id
    assert again.due_date == date(2026, 7, 1)
