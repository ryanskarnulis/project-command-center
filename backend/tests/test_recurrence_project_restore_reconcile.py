"""Project restore reconciles the recurring series it brings back (issue #169).

``task_trash.restore_task`` reconciles a restored task so a done occurrence whose
successor vanished while it sat in trash gets its next open occurrence back. The
batch project-restore path used to skip that, leaving a live-but-stalled series.
"""

from collections.abc import Sequence
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Project, Task, TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import task_trash
from app.services import tasks as tasks_service


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


def _stalled_project_trash(db: Session) -> tuple[Project, str, int]:
    """Reproduce issue #169's setup.

    Returns ``(trashed_project, recurrence_id, original_task_id)``: the project is
    in the trash holding the cascade-deleted, *done* 2026-07-01 occurrence, and its
    2026-07-08 successor has been permanently purged.
    """
    project = projects_service.create_project(db, name="Chores")
    original = tasks_service.create_task(
        db, project_id=project.id, title="water plants", due_date=date(2026, 7, 1)
    )
    tasks_service.update_task(
        db, original, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    recurrence_id = original.recurrence_id
    assert recurrence_id is not None
    original_id = original.id

    tasks_service.mark_done(db, original)
    db.commit()
    successor = _live_series(db, recurrence_id)[-1]
    assert successor.due_date == date(2026, 7, 8)

    # Trash the successor independently (null marker), then delete the project,
    # which cascade-deletes only the done predecessor.
    tasks_service.soft_delete_task(db, successor)
    db.commit()
    projects_service.soft_delete_project(db, project)
    db.commit()

    # Permanently purge the independently trashed successor: the series now has
    # nothing live at all, and nothing to spawn from while the project is trashed.
    trashed_successor = task_trash.get_deleted_task(db, successor.id)
    assert trashed_successor is not None
    task_trash.purge_task(db, trashed_successor)
    db.commit()
    assert _live_series(db, recurrence_id) == []

    trashed = projects_service.get_deleted_project(db, project.id)
    assert trashed is not None
    return trashed, recurrence_id, original_id


# --- Service ----------------------------------------------------------------


def test_restore_project_respawns_missing_successor(db_session: Session) -> None:
    project, recurrence_id, original_id = _stalled_project_trash(db_session)

    _restored, count = projects_service.restore_project(
        db_session, project, restore_tasks=True
    )
    db_session.commit()

    assert count == 1
    live = _live_series(db_session, recurrence_id)
    assert [t.due_date for t in live] == [date(2026, 7, 1), date(2026, 7, 8)]
    # The restored predecessor stays done; the series has a fresh open successor.
    by_date = {t.due_date: t for t in live}
    assert by_date[date(2026, 7, 1)].id == original_id
    assert by_date[date(2026, 7, 1)].workflow_status is TaskWorkflowStatus.done
    assert by_date[date(2026, 7, 8)].workflow_status is TaskWorkflowStatus.open
    assert by_date[date(2026, 7, 8)].project_id == project.id


def test_restore_project_is_idempotent_for_open_and_plain_tasks(
    db_session: Session,
) -> None:
    """No-op for restored open recurring / non-recurring tasks: no extra rows."""
    project = projects_service.create_project(db_session, name="Chores")
    recurring = tasks_service.create_task(
        db_session, project_id=project.id, title="water plants", due_date=date(2026, 7, 1)
    )
    tasks_service.update_task(
        db_session, recurring, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    tasks_service.create_task(db_session, project_id=project.id, title="one off")
    db_session.commit()
    recurrence_id = recurring.recurrence_id
    assert recurrence_id is not None

    projects_service.soft_delete_project(db_session, project)
    db_session.commit()
    trashed = projects_service.get_deleted_project(db_session, project.id)
    assert trashed is not None

    _restored, count = projects_service.restore_project(
        db_session, trashed, restore_tasks=True
    )
    db_session.commit()

    assert count == 2
    # The open occurrence did not spawn a successor.
    assert [t.due_date for t in _live_series(db_session, recurrence_id)] == [
        date(2026, 7, 1)
    ]
    active_titles = {
        t.title
        for t in db_session.execute(
            select(Task).where(
                Task.project_id == project.id, Task.deleted_at.is_(None)
            )
        )
        .scalars()
        .all()
    }
    assert active_titles == {"water plants", "one off"}


# --- Route ------------------------------------------------------------------


def test_restore_project_route_respawns_missing_successor(
    client: TestClient, db_session: Session
) -> None:
    project, recurrence_id, _original_id = _stalled_project_trash(db_session)

    response = client.post(
        f"/api/projects/{project.id}/restore", params={"restore_tasks": True}
    )

    assert response.status_code == 200
    db_session.expire_all()
    live = _live_series(db_session, recurrence_id)
    assert [t.due_date for t in live] == [date(2026, 7, 1), date(2026, 7, 8)]
    assert live[-1].workflow_status is TaskWorkflowStatus.open
