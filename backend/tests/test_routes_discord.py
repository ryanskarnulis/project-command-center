import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai import gateway
from app.config import Settings, get_settings
from app.db.models import AITrainingExample, InboxItem, InboxSource, TaskStatus
from app.main import app
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
    assert {c["status"] for c in candidates} == {TaskStatus.candidate}


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
