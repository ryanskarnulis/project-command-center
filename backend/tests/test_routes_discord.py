import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.workflows import match_project as match_workflow
from app.config import Settings, get_settings
from app.db.models import (
    AITrainingExample,
    InboxItem,
    InboxSource,
    TaskReviewStatus,
    TaskWorkflowStatus,
)
from app.main import app
from app.schemas.common import MAX_INBOX_RAW_TEXT_LENGTH
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.services.common import active

_SECRET = "test-secret"
_HEADERS = {"X-Backend-Secret": _SECRET}

_VALID_OUTPUT = {
    "summary": "Two follow-ups from the standup.",
    "project_hint": "Firewall",
    "tasks": [
        {
            "title": "Email the budget to Sarah",
            "description": "Quarterly spreadsheet",
            "due_date": "2026-06-05",
            "priority": "high",
            "assignee_hint": "Sarah",
            "confidence": 0.9,
        },
        {
            "title": "Ping ops about the deploy",
            "description": None,
            "due_date": None,
            "priority": "medium",
            "assignee_hint": None,
            "confidence": 0.6,
        },
    ],
    "needs_review": False,
}


@pytest.fixture
def with_secret() -> Generator[None, None, None]:
    """Override the shared secret so the discord route is enabled in tests."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        backend_shared_secret=_SECRET
    )
    yield
    app.dependency_overrides.pop(get_settings, None)


def test_discord_inbox_happy_path(
    client: TestClient,
    db_session: Session,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    resp = client.post(
        "/api/discord/inbox",
        json={"raw_text": "firewall cleanup notes"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "Two follow-ups from the standup."
    assert body["project_hint"] == "Firewall"
    assert body["candidate_count"] == 2
    assert body["task_titles"] == [
        "Email the budget to Sarah",
        "Ping ops about the deploy",
    ]

    # Inbox row persisted with the discord source; candidates are still candidates.
    item = db_session.execute(
        active(InboxItem).where(InboxItem.id == body["inbox_item_id"])
    ).scalar_one()
    assert item.source == InboxSource.discord
    candidates = client.get(f"/api/inbox/{item.id}/candidates").json()
    assert {c["review_status"] for c in candidates} == {TaskReviewStatus.candidate}


def test_discord_inbox_strips_raw_text(
    client: TestClient,
    db_session: Session,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    resp = client.post(
        "/api/discord/inbox",
        json={"raw_text": "  firewall cleanup notes  "},
        headers=_HEADERS,
    )

    assert resp.status_code == 200
    item = db_session.execute(
        active(InboxItem).where(InboxItem.id == resp.json()["inbox_item_id"])
    ).scalar_one()
    assert item.raw_text == "firewall cleanup notes"


def test_discord_inbox_rejects_blank_raw_text_before_extraction(
    client: TestClient,
    db_session: Session,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_complete(*_: object, **__: object) -> str:
        raise AssertionError("extraction should not run for invalid input")

    monkeypatch.setattr(gateway, "complete", fail_complete)

    resp = client.post(
        "/api/discord/inbox",
        json={"raw_text": "   "},
        headers=_HEADERS,
    )

    assert resp.status_code == 422
    assert db_session.execute(active(InboxItem)).scalars().all() == []


def test_discord_inbox_enforces_raw_text_max_length(
    client: TestClient,
    db_session: Session,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    accepted = client.post(
        "/api/discord/inbox",
        json={"raw_text": "x" * MAX_INBOX_RAW_TEXT_LENGTH},
        headers=_HEADERS,
    )
    assert accepted.status_code == 200

    def fail_complete(*_: object, **__: object) -> str:
        raise AssertionError("extraction should not run for invalid input")

    monkeypatch.setattr(gateway, "complete", fail_complete)

    too_long = client.post(
        "/api/discord/inbox",
        json={"raw_text": "x" * (MAX_INBOX_RAW_TEXT_LENGTH + 1)},
        headers=_HEADERS,
    )
    assert too_long.status_code == 422
    rows = db_session.execute(active(InboxItem)).scalars().all()
    assert len(rows) == 1


def test_discord_inbox_runs_project_matching(
    client: TestClient,
    db_session: Session,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))
    project_id = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]

    resp = client.post(
        "/api/discord/inbox",
        json={"raw_text": "firewall cleanup notes"},
        headers=_HEADERS,
    )

    assert resp.status_code == 200
    item = db_session.execute(
        active(InboxItem).where(InboxItem.id == resp.json()["inbox_item_id"])
    ).scalar_one()
    assert item.suggested_project_id == project_id

    candidates = client.get(f"/api/inbox/{item.id}/candidates").json()
    assert {c["review_status"] for c in candidates} == {TaskReviewStatus.candidate}


def test_discord_inbox_match_failure_is_nonfatal(
    client: TestClient,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    def fail_match(*_: object, **__: object) -> None:
        raise RuntimeError("matching unavailable")

    monkeypatch.setattr(match_workflow, "match_inbox_item", fail_match)

    resp = client.post(
        "/api/discord/inbox",
        json={"raw_text": "firewall cleanup notes"},
        headers=_HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_count"] == 2
    assert body["task_titles"] == [
        "Email the budget to Sarah",
        "Ping ops about the deploy",
    ]


def test_discord_inbox_upstream_failure_502(
    client: TestClient,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**_: object) -> str:
        raise gateway.GatewayError("ollama unreachable")

    monkeypatch.setattr(gateway, "complete", boom)

    resp = client.post(
        "/api/discord/inbox",
        json={"raw_text": "firewall cleanup notes"},
        headers=_HEADERS,
    )
    assert resp.status_code == 502


def test_discord_inbox_idempotent(
    client: TestClient,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    payload = {"raw_text": "same text twice"}
    first = client.post("/api/discord/inbox", json=payload, headers=_HEADERS).json()
    second = client.post("/api/discord/inbox", json=payload, headers=_HEADERS).json()

    assert first["inbox_item_id"] == second["inbox_item_id"]
    # No duplicate candidates from the second call.
    candidates = client.get(f"/api/inbox/{first['inbox_item_id']}/candidates").json()
    assert len(candidates) == 2


def test_discord_inbox_wrong_secret_401(
    client: TestClient, db_session: Session, with_secret: None
) -> None:
    resp = client.post(
        "/api/discord/inbox",
        json={"raw_text": "should not be stored"},
        headers={"X-Backend-Secret": "wrong"},
    )
    assert resp.status_code == 401
    # Nothing was created.
    assert db_session.execute(active(InboxItem)).scalars().all() == []


def test_discord_inbox_missing_secret_401(
    client: TestClient, with_secret: None
) -> None:
    resp = client.post("/api/discord/inbox", json={"raw_text": "no header"})
    assert resp.status_code == 401


def test_discord_inbox_unconfigured_503(client: TestClient) -> None:
    # No with_secret override → backend_shared_secret defaults to "".
    app.dependency_overrides[get_settings] = lambda: Settings(backend_shared_secret="")
    try:
        resp = client.post(
            "/api/discord/inbox", json={"raw_text": "x"}, headers=_HEADERS
        )
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_discord_inbox_malformed_extraction_422_and_training_row(
    client: TestClient,
    db_session: Session,
    with_secret: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = '{"summary": "oops", "tasks": "not a list", "needs_review": true}'
    monkeypatch.setattr(gateway, "complete", lambda **_: bad)

    resp = client.post(
        "/api/discord/inbox", json={"raw_text": "bad notes"}, headers=_HEADERS
    )
    assert resp.status_code == 422

    examples = db_session.execute(active(AITrainingExample)).scalars().all()
    assert len(examples) == 1
    assert examples[0].accepted is False
    assert examples[0].model_output_json == bad


# --- /api/discord/tasks (list) and /api/discord/tasks/search --------------------


def test_discord_tasks_lists_only_open_tasks(
    client: TestClient, db_session: Session, with_secret: None
) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    tasks_service.create_task(db_session, project_id=project.id, title="audit rules")
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="already finished",
        workflow_status=TaskWorkflowStatus.done,
    )
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="still a candidate",
        review_status=TaskReviewStatus.candidate,
    )
    deleted = tasks_service.create_task(
        db_session, project_id=project.id, title="trashed"
    )
    tasks_service.soft_delete_task(db_session, deleted)
    db_session.commit()

    resp = client.get("/api/discord/tasks", headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert [t["title"] for t in body["tasks"]] == ["audit rules"]
    assert body["total"] == 1
    assert body["tasks"][0]["project_name"] == "Firewall"


def test_discord_tasks_filter_by_name_and_alias(
    client: TestClient, db_session: Session, with_secret: None
) -> None:
    firewall = projects_service.create_project(db_session, name="Firewall")
    projects_service.create_alias(db_session, project_id=firewall.id, alias="FW")
    kitchen = projects_service.create_project(db_session, name="Kitchen")
    tasks_service.create_task(db_session, project_id=firewall.id, title="audit rules")
    tasks_service.create_task(db_session, project_id=kitchen.id, title="buy milk")
    db_session.commit()

    by_name = client.get("/api/discord/tasks?project=Firewall", headers=_HEADERS).json()
    assert [t["title"] for t in by_name["tasks"]] == ["audit rules"]

    # Alias resolves to the same project; matching is case/space-insensitive.
    by_alias = client.get("/api/discord/tasks?project=fw", headers=_HEADERS).json()
    assert [t["title"] for t in by_alias["tasks"]] == ["audit rules"]

    unknown = client.get("/api/discord/tasks?project=Nope", headers=_HEADERS).json()
    assert unknown == {"tasks": [], "total": 0}


def test_discord_tasks_search_ranks_and_filters(
    client: TestClient, db_session: Session, with_secret: None
) -> None:
    project = projects_service.create_project(db_session, name="Ops")
    tasks_service.create_task(db_session, project_id=project.id, title="Deploy the firewall")
    tasks_service.create_task(db_session, project_id=project.id, title="Deploy")
    tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="Deploy old build",
        workflow_status=TaskWorkflowStatus.done,
    )
    db_session.commit()

    body = client.get("/api/discord/tasks/search?q=Deploy", headers=_HEADERS).json()
    titles = [t["title"] for t in body["tasks"]]
    # Exact-title match ranks first; the done task is excluded.
    assert titles == ["Deploy", "Deploy the firewall"]


def test_discord_tasks_search_blank_query_returns_empty(
    client: TestClient, db_session: Session, with_secret: None
) -> None:
    project = projects_service.create_project(db_session, name="Ops")
    tasks_service.create_task(db_session, project_id=project.id, title="Deploy")
    db_session.commit()

    body = client.get("/api/discord/tasks/search?q=%20%20", headers=_HEADERS).json()
    assert body == {"tasks": []}


def test_discord_tasks_requires_secret(
    client: TestClient, with_secret: None
) -> None:
    assert client.get("/api/discord/tasks").status_code == 401
    assert client.get("/api/discord/tasks/search?q=x").status_code == 401
    assert (
        client.get("/api/discord/tasks", headers={"X-Backend-Secret": "wrong"}).status_code
        == 401
    )


def test_discord_tasks_unconfigured_503(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(backend_shared_secret="")
    try:
        assert client.get("/api/discord/tasks", headers=_HEADERS).status_code == 503
        assert (
            client.get("/api/discord/tasks/search?q=x", headers=_HEADERS).status_code
            == 503
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_discord_done_path_preserves_recurrence(
    client: TestClient, db_session: Session
) -> None:
    """The bot's /done hits POST /api/tasks/{id}/done, which spawns the next occurrence."""
    from datetime import date

    project = projects_service.create_project(db_session, name="Ops")
    task = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="weekly backup",
        due_date=date(2026, 7, 3),
    )
    task.repeat_interval = {"unit": "week", "every": 1}
    db_session.commit()

    resp = client.post(f"/api/tasks/{task.id}/done")
    assert resp.status_code == 200

    remaining = tasks_service.list_tasks(
        db_session,
        project.id,
        review_status=TaskReviewStatus.accepted,
        exclude_done=True,
    )
    # The completed task is gone from the open list; a fresh occurrence took its place.
    next_occurrences = [t for t in remaining if t.title == "weekly backup"]
    assert len(next_occurrences) == 1
    assert next_occurrences[0].id != task.id
    assert next_occurrences[0].due_date == date(2026, 7, 10)
