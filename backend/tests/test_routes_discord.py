import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.workflows import match_project as match_workflow
from app.config import Settings, get_settings
from app.db.models import AITrainingExample, InboxItem, InboxSource, TaskReviewStatus
from app.main import app
from app.schemas.common import MAX_INBOX_RAW_TEXT_LENGTH
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
