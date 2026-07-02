from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import TaskReviewStatus, TaskWorkflowStatus
from app.services import tasks as tasks_service
from app.services.common import soft_delete


def _task(db: Session, title: str, **kwargs: Any) -> int:
    task = tasks_service.create_task(db, project_id=None, title=title, **kwargs)
    db.commit()
    return task.id


def test_calendar_returns_only_tasks_due_in_range(
    client: TestClient, db_session: Session
) -> None:
    inside = _task(db_session, "inside", due_date=date(2026, 6, 15))
    _task(db_session, "before window", due_date=date(2026, 5, 31))
    _task(db_session, "after window", due_date=date(2026, 7, 1))
    _task(db_session, "no due date")

    response = client.get(
        "/api/calendar", params={"start": "2026-06-01", "end": "2026-06-30"}
    )

    assert response.status_code == 200
    assert [t["id"] for t in response.json()] == [inside]


def test_calendar_includes_done_excludes_candidate(
    client: TestClient, db_session: Session
) -> None:
    done = _task(
        db_session,
        "done work",
        due_date=date(2026, 6, 10),
        workflow_status=TaskWorkflowStatus.done,
    )
    _task(
        db_session,
        "candidate",
        due_date=date(2026, 6, 11),
        review_status=TaskReviewStatus.candidate,
    )

    response = client.get(
        "/api/calendar", params={"start": "2026-06-01", "end": "2026-06-30"}
    )

    assert response.status_code == 200
    # Done is included (shown on its day); candidate is filed-review noise, excluded.
    assert [t["id"] for t in response.json()] == [done]


def test_calendar_excludes_soft_deleted(
    client: TestClient, db_session: Session
) -> None:
    task_id = _task(db_session, "trashed", due_date=date(2026, 6, 12))
    task = tasks_service.get_task(db_session, task_id)
    assert task is not None
    soft_delete(task)
    db_session.commit()

    response = client.get(
        "/api/calendar", params={"start": "2026-06-01", "end": "2026-06-30"}
    )

    assert response.status_code == 200
    assert response.json() == []


def test_calendar_rejects_end_before_start(
    client: TestClient, db_session: Session
) -> None:
    response = client.get(
        "/api/calendar", params={"start": "2026-06-30", "end": "2026-06-01"}
    )
    assert response.status_code == 422


def test_calendar_rejects_malformed_date(
    client: TestClient, db_session: Session
) -> None:
    assert (
        client.get(
            "/api/calendar", params={"start": "nope", "end": "2026-06-01"}
        ).status_code
        == 422
    )
