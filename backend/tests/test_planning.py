from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import TaskReviewStatus, TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service
from app.services.common import soft_delete


def _project(db: Session, name: str) -> int:
    project = projects_service.create_project(db, name=name, description=None)
    db.commit()
    return project.id


def _task(db: Session, project_id: int | None, title: str, **kwargs: object) -> int:
    # ``scheduled_start`` is not a create_task arg this slice (set only via PATCH);
    # stamp it directly so the planning read can be exercised.
    scheduled_start = kwargs.pop("scheduled_start", None)
    task = tasks_service.create_task(db, project_id=project_id, title=title, **kwargs)
    if scheduled_start is not None:
        task.scheduled_start = scheduled_start  # type: ignore[assignment]
    db.commit()
    return task.id


def test_gantt_returns_accepted_not_done_for_project(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    other = _project(db_session, "Other")

    placed = _task(
        db_session,
        project,
        "scheduled",
        scheduled_start=date(2026, 6, 20),
        estimated_minutes=480,
    )
    due_only = _task(db_session, project, "due only", due_date=date(2026, 6, 25))
    _task(
        db_session,
        project,
        "done work",
        workflow_status=TaskWorkflowStatus.done,
    )
    _task(
        db_session,
        project,
        "candidate",
        review_status=TaskReviewStatus.candidate,
    )
    _task(db_session, other, "other project task", due_date=date(2026, 6, 22))

    response = client.get(f"/api/projects/{project}/gantt")

    assert response.status_code == 200
    body = response.json()
    assert sorted(t["id"] for t in body["tasks"]) == sorted([placed, due_only])
    placed_read = next(t for t in body["tasks"] if t["id"] == placed)
    assert placed_read["scheduled_start"] == "2026-06-20"


def test_gantt_excludes_soft_deleted(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    kept = _task(db_session, project, "kept", due_date=date(2026, 6, 25))
    trashed = _task(db_session, project, "trashed", due_date=date(2026, 6, 26))
    task = tasks_service.get_task(db_session, trashed)
    assert task is not None
    soft_delete(task)
    db_session.commit()

    response = client.get(f"/api/projects/{project}/gantt")

    assert response.status_code == 200
    assert [t["id"] for t in response.json()["tasks"]] == [kept]


def test_gantt_returns_edges_only_between_payload_tasks(
    client: TestClient, db_session: Session
) -> None:
    project = _project(db_session, "Firewall")
    a = _task(db_session, project, "a", due_date=date(2026, 6, 20))
    b = _task(db_session, project, "b", due_date=date(2026, 6, 21))
    done = _task(
        db_session, project, "done", workflow_status=TaskWorkflowStatus.done
    )
    deps_service.add_dependency(db_session, a, b)
    # Edge to a done task: done is filtered out of the payload, so this edge must
    # not be returned (no bar to attach to).
    deps_service.add_dependency(db_session, a, done)
    db_session.commit()

    response = client.get(f"/api/projects/{project}/gantt")

    assert response.status_code == 200
    deps = response.json()["dependencies"]
    assert deps == [{"task_id": a, "depends_on_task_id": b}]


def test_gantt_404_unknown_project(
    client: TestClient, db_session: Session
) -> None:
    assert client.get("/api/projects/9999/gantt").status_code == 404
