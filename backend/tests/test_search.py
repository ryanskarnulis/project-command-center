from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import inbox as inbox_service
from app.services import projects as projects_service
from app.services import search as search_service
from app.services import tasks as tasks_service


def _seed(db: Session) -> None:
    project = projects_service.create_project(
        db, name="Firewall Upgrade", description="network hardening"
    )
    tasks_service.create_task(
        db, project_id=project.id, title="Audit firewall rules"
    )
    inbox_service.create_inbox_item(db, raw_text="remember to patch the firewall")
    db.commit()


def test_search_matches_each_kind(db_session: Session) -> None:
    _seed(db_session)

    results = search_service.search(db_session, "firewall")

    assert [p.title for p in results.projects] == ["Firewall Upgrade"]
    assert [t.title for t in results.tasks] == ["Audit firewall rules"]
    assert len(results.inbox_items) == 1
    # Task subtitle resolves to the owning project's name.
    assert results.tasks[0].subtitle == "Firewall Upgrade"
    assert results.tasks[0].project_id is not None


def test_search_tasks_carry_status_fields(db_session: Session) -> None:
    """Tasks expose review/workflow status (for /done); other kinds leave them None."""
    _seed(db_session)

    results = search_service.search(db_session, "firewall")

    task = results.tasks[0]
    assert task.review_status == "accepted"
    assert task.workflow_status == "open"
    # The status fields are task-only; projects and inbox items serialize as null.
    assert results.projects[0].review_status is None
    assert results.projects[0].workflow_status is None
    assert results.inbox_items[0].review_status is None
    assert results.inbox_items[0].workflow_status is None


def test_search_is_case_insensitive(db_session: Session) -> None:
    _seed(db_session)

    results = search_service.search(db_session, "FIREWALL")

    assert len(results.projects) == 1
    assert len(results.tasks) == 1


def test_search_blank_query_returns_empty(db_session: Session) -> None:
    _seed(db_session)

    results = search_service.search(db_session, "   ")

    assert results.projects == []
    assert results.tasks == []
    assert results.inbox_items == []


def test_search_excludes_soft_deleted(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Firewall Upgrade")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="Audit firewall rules"
    )
    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == []
    assert [p.title for p in results.projects] == ["Firewall Upgrade"]


def test_search_escapes_like_wildcards(db_session: Session) -> None:
    # A bare "%" must not behave as a match-all wildcard.
    projects_service.create_project(db_session, name="Firewall Upgrade")
    projects_service.create_project(db_session, name="50% capacity plan")
    db_session.commit()

    # "%" is treated as a literal: it matches the row that actually contains a
    # percent sign, and must NOT match everything (which an unescaped wildcard would).
    wildcard = search_service.search(db_session, "%")
    literal = search_service.search(db_session, "50%")

    assert [p.title for p in wildcard.projects] == ["50% capacity plan"]
    assert [p.title for p in literal.projects] == ["50% capacity plan"]


def test_search_route_happy_path(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    response = client.get("/api/search", params={"q": "firewall"})

    assert response.status_code == 200
    body = response.json()
    assert body["projects"][0]["kind"] == "project"
    assert body["tasks"][0]["kind"] == "task"
    assert body["inbox_items"][0]["kind"] == "inbox"


def test_search_route_blank_query(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    response = client.get("/api/search", params={"q": ""})

    assert response.status_code == 200
    assert response.json() == {"projects": [], "tasks": [], "inbox_items": []}
