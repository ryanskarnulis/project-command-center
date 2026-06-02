from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import TaskPriority, TaskStatus
from app.services import projects as projects_service
from app.services import tasks as tasks_service


def test_task_create_markdone_softdelete(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    db_session.commit()

    task = tasks_service.create_task(
        db_session, project_id=project.id, title="audit rules"
    )
    db_session.commit()
    assert task.id is not None
    assert task.project_id == project.id
    assert task.status == TaskStatus.accepted
    assert task.priority == TaskPriority.medium

    assert task.id in [t.id for t in tasks_service.list_tasks(db_session, project.id)]

    done = tasks_service.mark_done(db_session, task)
    db_session.commit()
    assert done.status == TaskStatus.done

    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    assert tasks_service.get_task(db_session, task.id) is None
    assert task.id not in [
        t.id for t in tasks_service.list_tasks(db_session, project.id)
    ]


def test_accepted_task_without_project_defaults_to_general(
    db_session: Session,
) -> None:
    task = tasks_service.create_task(
        db_session, project_id=None, title="unfiled visible work"
    )
    db_session.commit()

    general = projects_service.get_default_project(db_session)
    assert general is not None
    assert task.project_id == general.id


def test_candidate_without_project_stays_unfiled_until_review(
    db_session: Session,
) -> None:
    task = tasks_service.create_task(
        db_session,
        project_id=None,
        title="candidate work",
        status=TaskStatus.candidate,
    )
    db_session.commit()

    assert task.project_id is None


def test_candidate_updated_to_accepted_defaults_to_general(
    db_session: Session,
) -> None:
    task = tasks_service.create_task(
        db_session,
        project_id=None,
        title="accepted later",
        status=TaskStatus.candidate,
    )
    tasks_service.update_task(db_session, task, {"status": TaskStatus.accepted})
    db_session.commit()

    general = projects_service.get_default_project(db_session)
    assert general is not None
    assert task.project_id == general.id


def test_global_tasks_route_lists_accepted_tasks_across_projects(
    client: TestClient, db_session: Session
) -> None:
    a = projects_service.create_project(db_session, name="Firewall")
    b = projects_service.create_project(db_session, name="Kitchen")
    open_a = tasks_service.create_task(db_session, project_id=a.id, title="audit rules")
    open_b = tasks_service.create_task(db_session, project_id=b.id, title="buy filters")
    tasks_service.create_task(
        db_session,
        project_id=b.id,
        title="done already",
        status=TaskStatus.done,
    )
    db_session.commit()

    resp = client.get("/api/tasks")

    assert resp.status_code == 200
    ids = [task["id"] for task in resp.json()]
    assert ids == [open_a.id, open_b.id]


def test_deleted_project_tasks_remain_reachable_from_global_route(
    client: TestClient, db_session: Session
) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="reachable work"
    )
    db_session.commit()

    projects_service.soft_delete_project(db_session, project)
    db_session.commit()

    resp = client.get("/api/tasks")

    assert resp.status_code == 200
    rows = resp.json()
    assert [row["id"] for row in rows] == [task.id]
    assert rows[0]["project_id"] != project.id


def test_global_tasks_route_creates_task_in_general(client: TestClient) -> None:
    resp = client.post("/api/tasks", json={"title": "sort loose work"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "sort loose work"
    assert body["project_id"] is not None


def test_task_routes_strip_and_reject_blank_text(client: TestClient) -> None:
    created = client.post(
        "/api/tasks",
        json={"title": "  Check firewall  ", "description": "   "},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["title"] == "Check firewall"
    assert created.json()["description"] is None

    updated = client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "  Check edge router  ", "description": "  spare sfp  "},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Check edge router"
    assert updated.json()["description"] == "spare sfp"

    blank_create = client.post("/api/tasks", json={"title": "   "})
    assert blank_create.status_code == 422

    blank_update = client.patch(f"/api/tasks/{task_id}", json={"title": "   "})
    assert blank_update.status_code == 422
