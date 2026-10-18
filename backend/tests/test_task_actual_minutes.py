"""A timed completion persists what the work actually took (issue #318)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, Task
from app.services import projects as projects_service
from app.services import tasks as tasks_service


def _task(db_session: Session, title: str = "write report") -> Task:
    project = projects_service.create_project(db_session, name="Focus")
    task = tasks_service.create_task(db_session, project_id=project.id, title=title)
    db_session.commit()
    return task


def _completed_events(db_session: Session, task_id: int) -> list[ActivityEvent]:
    return list(
        db_session.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.entity_id == task_id)
            .where(ActivityEvent.entity_type == "task")
            .where(ActivityEvent.action == "completed")
        )
    )


def test_timed_completion_persists_and_exposes_actual_minutes(
    client: TestClient, db_session: Session
) -> None:
    task = _task(db_session)

    done = client.post(f"/api/tasks/{task.id}/done", json={"actual_minutes": 47})
    assert done.status_code == 200
    assert done.json()["workflow_status"] == "done"
    assert done.json()["actual_minutes"] == 47

    assert client.get(f"/api/tasks/{task.id}").json()["actual_minutes"] == 47
    events = _completed_events(db_session, task.id)
    assert [e.summary for e in events] == ['Task "write report" completed in 47 min']


def test_untimed_completion_leaves_actual_null(
    client: TestClient, db_session: Session
) -> None:
    task = _task(db_session)

    done = client.post(f"/api/tasks/{task.id}/done")
    assert done.status_code == 200
    assert done.json()["actual_minutes"] is None
    assert [e.summary for e in _completed_events(db_session, task.id)] == [
        'Task "write report" completed'
    ]


def test_reopen_keeps_actual_and_next_timed_completion_overwrites(
    client: TestClient, db_session: Session
) -> None:
    task = _task(db_session)
    client.post(f"/api/tasks/{task.id}/done", json={"actual_minutes": 30})

    reopened = client.post(f"/api/tasks/{task.id}/reopen")
    assert reopened.json()["actual_minutes"] == 30

    # An untimed re-completion has nothing to say about duration: kept.
    client.post(f"/api/tasks/{task.id}/done")
    assert client.get(f"/api/tasks/{task.id}").json()["actual_minutes"] == 30

    client.post(f"/api/tasks/{task.id}/reopen")
    again = client.post(f"/api/tasks/{task.id}/done", json={"actual_minutes": 12})
    assert again.json()["actual_minutes"] == 12


def test_repeat_completion_does_not_rewrite_actual(
    client: TestClient, db_session: Session
) -> None:
    task = _task(db_session)
    client.post(f"/api/tasks/{task.id}/done", json={"actual_minutes": 30})

    # Already done: the call is a no-op, so it must not move the column
    # without the activity event that would explain it.
    repeat = client.post(f"/api/tasks/{task.id}/done", json={"actual_minutes": 99})
    assert repeat.status_code == 200
    assert repeat.json()["actual_minutes"] == 30
    assert len(_completed_events(db_session, task.id)) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"actual_minutes": -1},
        {"actual_minutes": 1441},
        {"actual_minutes": 2.5},
        {"actual_minutes": 10, "estimated_minutes": 5},
    ],
)
def test_invalid_actual_minutes_rejected_and_task_unchanged(
    client: TestClient, db_session: Session, body: dict[str, object]
) -> None:
    task = _task(db_session)

    response = client.post(f"/api/tasks/{task.id}/done", json=body)
    assert response.status_code == 422
    after = client.get(f"/api/tasks/{task.id}").json()
    assert after["workflow_status"] == "open"
    assert after["actual_minutes"] is None


def test_zero_and_ceiling_are_accepted(
    client: TestClient, db_session: Session
) -> None:
    first = _task(db_session, "instant")
    second = tasks_service.create_task(
        db_session, project_id=first.project_id, title="all day"
    )
    db_session.commit()

    zero = client.post(f"/api/tasks/{first.id}/done", json={"actual_minutes": 0})
    assert zero.json()["actual_minutes"] == 0
    day = client.post(f"/api/tasks/{second.id}/done", json={"actual_minutes": 1440})
    assert day.json()["actual_minutes"] == 1440


def test_recurring_successor_does_not_inherit_actual(
    client: TestClient, db_session: Session
) -> None:
    task = _task(db_session, "standup")
    patched = client.patch(
        f"/api/tasks/{task.id}",
        json={"due_date": "2026-10-18", "repeat_interval": {"unit": "day", "every": 1}},
    )
    assert patched.status_code == 200

    done = client.post(f"/api/tasks/{task.id}/done", json={"actual_minutes": 15})
    assert done.json()["actual_minutes"] == 15

    series = client.get(f"/api/tasks/{task.id}/series").json()["occurrences"]
    successors = [t for t in series if t["id"] != task.id]
    assert successors, "completing a recurring task spawns its next occurrence"
    assert all(t["actual_minutes"] is None for t in successors)
