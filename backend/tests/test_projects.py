from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import projects as projects_service


def test_project_create_get_list_softdelete(db_session: Session) -> None:
    created = projects_service.create_project(
        db_session, name="Firewall", description="net cleanup"
    )
    assert created.id is not None
    assert created.name == "Firewall"
    assert created.description == "net cleanup"
    assert created.created_at is not None
    assert created.deleted_at is None

    fetched = projects_service.get_project(db_session, created.id)
    assert fetched is not None
    assert fetched.id == created.id

    assert created.id in [p.id for p in projects_service.list_projects(db_session)]

    projects_service.soft_delete_project(db_session, created)

    assert projects_service.get_project(db_session, created.id) is None
    assert created.id not in [p.id for p in projects_service.list_projects(db_session)]


def test_alias_create_list_softdelete(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Home Network")
    alias = projects_service.create_alias(
        db_session, project_id=project.id, alias="firewall"
    )
    assert alias.id is not None
    assert alias.project_id == project.id

    listed = projects_service.list_aliases(db_session, project.id)
    assert [a.id for a in listed] == [alias.id]

    projects_service.soft_delete_alias(db_session, alias)
    assert projects_service.list_aliases(db_session, project.id) == []
    assert projects_service.get_alias(db_session, alias.id) is None


def test_list_projects_with_aliases_groups_aliases(db_session: Session) -> None:
    a = projects_service.create_project(db_session, name="Home Network")
    b = projects_service.create_project(db_session, name="Empty")
    projects_service.create_alias(db_session, project_id=a.id, alias="firewall")
    projects_service.create_alias(db_session, project_id=a.id, alias="homelab")

    pairs = dict(
        (p.id, aliases) for p, aliases in projects_service.list_projects_with_aliases(db_session)
    )
    assert sorted(pairs[a.id]) == ["firewall", "homelab"]
    assert pairs[b.id] == []


def test_match_hint_matches_name_and_alias(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Home Network")
    projects_service.create_alias(db_session, project_id=project.id, alias="firewall")

    # Exact (normalized) name match.
    assert (
        projects_service.match_text_to_project(db_session, "  home   NETWORK ")
        is not None
    )
    # Alias appears within a longer hint.
    matched = projects_service.match_text_to_project(
        db_session, "firewall ruleset cleanup before the audit"
    )
    assert matched is not None and matched.id == project.id


def test_match_hint_no_or_empty_hint_returns_none(db_session: Session) -> None:
    projects_service.create_project(db_session, name="Home Network")
    assert projects_service.match_text_to_project(db_session, None) is None
    assert projects_service.match_text_to_project(db_session, "   ") is None
    assert projects_service.match_text_to_project(db_session, "totally unrelated") is None


def test_match_hint_ambiguous_returns_none(db_session: Session) -> None:
    # Same alias on two projects → ambiguous → defer to the AI matcher.
    a = projects_service.create_project(db_session, name="Home Network")
    b = projects_service.create_project(db_session, name="Security")
    projects_service.create_alias(db_session, project_id=a.id, alias="firewall")
    projects_service.create_alias(db_session, project_id=b.id, alias="firewall")

    assert projects_service.match_text_to_project(db_session, "firewall work") is None


def test_alias_routes_crud_and_404s(client: TestClient) -> None:
    project_id = client.post("/api/projects", json={"name": "Home Network"}).json()["id"]

    created = client.post(
        f"/api/projects/{project_id}/aliases", json={"alias": "firewall"}
    )
    assert created.status_code == 201
    alias_id = created.json()["id"]
    assert created.json()["project_id"] == project_id

    listed = client.get(f"/api/projects/{project_id}/aliases")
    assert [a["id"] for a in listed.json()] == [alias_id]

    # Alias scoped to its project: deleting under the wrong project is a 404.
    other_id = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    assert (
        client.delete(f"/api/projects/{other_id}/aliases/{alias_id}").status_code == 404
    )

    assert client.delete(f"/api/projects/{project_id}/aliases/{alias_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}/aliases").json() == []

    # Aliases on a missing project, and missing aliases, are 404.
    assert client.post("/api/projects/9999/aliases", json={"alias": "x"}).status_code == 404
    assert client.delete(f"/api/projects/{project_id}/aliases/9999").status_code == 404
