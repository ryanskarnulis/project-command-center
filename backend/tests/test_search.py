from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import TaskWorkflowStatus
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
    db.commit()


def test_search_matches_each_kind(db_session: Session) -> None:
    _seed(db_session)

    results = search_service.search(db_session, "firewall")

    assert [p.title for p in results.projects] == ["Firewall Upgrade"]
    assert [t.title for t in results.tasks] == ["Audit firewall rules"]
    # Task subtitle resolves to the owning project's name.
    assert results.tasks[0].subtitle == "Firewall Upgrade"
    assert results.tasks[0].project_id is not None


def test_search_tasks_carry_status_fields(db_session: Session) -> None:
    """Tasks expose workflow status (for /done); other kinds leave it None."""
    _seed(db_session)

    results = search_service.search(db_session, "firewall")

    task = results.tasks[0]
    assert task.workflow_status == "open"
    # The status field is task-only; projects serialize as null.
    assert results.projects[0].workflow_status is None


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


def test_search_ranks_exact_title_above_newer_description_match(
    db_session: Session,
) -> None:
    """Relevance beats recency: an exact title wins over a newer description-only hit."""
    project = projects_service.create_project(db_session, name="P")
    # Older row matches the title exactly (best tier).
    tasks_service.create_task(db_session, project_id=project.id, title="firewall")
    # Newer row only matches on description; recency must not float it to the top.
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="zzz top",
        description="firewall config",
    )
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == ["firewall", "zzz top"]


def test_search_ranks_prefix_above_substring(db_session: Session) -> None:
    """A prefix match outranks a mid-string substring match, even when it's older."""
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(
        db_session, project_id=project.id, title="firewall audit"
    )  # prefix
    tasks_service.create_task(
        db_session, project_id=project.id, title="check the firewall now"
    )  # substring, newer
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == [
        "firewall audit",
        "check the firewall now",
    ]


def test_search_prefers_open_over_done_at_same_text_tier(
    db_session: Session,
) -> None:
    """At the same text tier, an accepted+open task outranks a done one (and stays
    ahead even though the done task is newer)."""
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(
        db_session, project_id=project.id, title="firewall ready"
    )  # accepted + open
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="firewall closed",
        workflow_status=TaskWorkflowStatus.done,
    )  # done, newer
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == ["firewall ready", "firewall closed"]


def test_search_text_relevance_beats_task_state_bias(db_session: Session) -> None:
    """A done exact-title hit still outranks an open description-only hit."""
    project = projects_service.create_project(db_session, name="P")
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="firewall",
        workflow_status=TaskWorkflowStatus.done,
    )
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="zzz top",
        description="firewall config",
    )
    db_session.commit()

    results = search_service.search(db_session, "firewall")

    assert [t.title for t in results.tasks] == ["firewall", "zzz top"]


def test_search_route_happy_path(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    response = client.get("/api/search", params={"q": "firewall"})

    assert response.status_code == 200
    body = response.json()
    assert body["projects"][0]["kind"] == "project"
    assert body["tasks"][0]["kind"] == "task"


def test_search_route_blank_query(client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    response = client.get("/api/search", params={"q": ""})

    assert response.status_code == 200
    assert response.json() == {"projects": [], "tasks": []}
