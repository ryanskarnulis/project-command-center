from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import TaskPriority
from app.services import tasks as tasks_service


def _task(db: Session, title: str, **kwargs: object) -> int:
    task = tasks_service.create_task(db, project_id=None, title=title, **kwargs)
    db.commit()
    return task.id


def test_today_route_happy_path(client: TestClient, db_session: Session) -> None:
    task_id = _task(db_session, "ship it", priority=TaskPriority.urgent)

    response = client.get("/api/today", params={"date": "2026-06-20"})

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2026-06-20"
    assert [b["task_id"] for b in body["scheduled"]] == [task_id]


def test_today_route_defaults_to_server_today(
    client: TestClient, db_session: Session
) -> None:
    _task(db_session, "anything")

    response = client.get("/api/today")

    assert response.status_code == 200
    assert response.json()["date"] == date.today().isoformat()


def test_today_route_passes_through_start_and_capacity(
    client: TestClient, db_session: Session
) -> None:
    _task(db_session, "first", priority=TaskPriority.urgent, estimated_minutes=60)
    _task(db_session, "second", priority=TaskPriority.low, estimated_minutes=60)

    response = client.get(
        "/api/today",
        params={"date": "2026-06-20", "start_time": "10:00", "available_minutes": 90},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["start_time"] == "10:00"
    assert body["scheduled"][0]["start_time"] == "10:00"
    # 90 minutes of capacity fits only the first 60-minute task.
    assert len(body["scheduled"]) == 1
    assert len(body["overflow"]) == 1


def test_today_route_rejects_malformed_start_time(
    client: TestClient, db_session: Session
) -> None:
    assert client.get("/api/today", params={"start_time": "25:00"}).status_code == 422
    assert client.get("/api/today", params={"start_time": "9am"}).status_code == 422


def test_today_route_rejects_out_of_range_capacity(
    client: TestClient, db_session: Session
) -> None:
    assert client.get("/api/today", params={"available_minutes": 0}).status_code == 422
    assert (
        client.get("/api/today", params={"available_minutes": 5000}).status_code == 422
    )


def test_today_route_rejects_malformed_date(
    client: TestClient, db_session: Session
) -> None:
    assert client.get("/api/today", params={"date": "not-a-date"}).status_code == 422
