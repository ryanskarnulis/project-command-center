import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import rate_limit
from app.config import Settings
from app.services import projects as projects_service


@pytest.fixture
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    # Drive both buckets down to 2/min so a breach is cheap to trigger.
    settings = Settings(
        rate_limit_discord_inbox_per_min=2,
        rate_limit_summary_per_min=2,
        rate_limit_inbox_process_per_min=2,
        rate_limit_breakdown_per_min=2,
        backend_shared_secret="s3cret",
    )
    monkeypatch.setattr(rate_limit, "get_settings", lambda: settings)


def test_summary_route_throttles_after_limit(
    client: TestClient,
    db_session: Session,
    small_limits: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    db_session.commit()

    # Avoid a real Ollama call — the limiter runs before the handler regardless.
    monkeypatch.setattr(
        "app.api.routes_ai.summarize_project_ai",
        lambda **_: "stub summary",
    )

    url = f"/api/projects/{project.id}/summary"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200

    resp = client.get(url)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_inbox_process_route_throttles_after_limit(
    client: TestClient,
    small_limits: None,
) -> None:
    # The limiter is a dependency that runs (and records its hit) before the
    # handler, so a missing id still counts toward the cap — no model call needed.
    url = "/api/inbox/999999/process"
    assert client.post(url).status_code == 404
    assert client.post(url).status_code == 404

    resp = client.post(url)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_breakdown_route_throttles_after_limit(
    client: TestClient,
    small_limits: None,
) -> None:
    url = "/api/tasks/999999/break-down"
    assert client.post(url).status_code == 404
    assert client.post(url).status_code == 404

    resp = client.post(url)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_buckets_are_independent(
    client: TestClient,
    db_session: Session,
    small_limits: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    db_session.commit()
    monkeypatch.setattr(
        "app.api.routes_ai.summarize_project_ai",
        lambda **_: "stub summary",
    )

    url = f"/api/projects/{project.id}/summary"
    # Exhaust the summary bucket.
    client.get(url)
    client.get(url)
    assert client.get(url).status_code == 429

    # The discord bucket is untouched by the summary breach: it short-circuits at
    # its own auth guard, never a 429 from a leaked cross-bucket counter.
    resp = client.post("/api/discord/inbox", json={"raw_text": "buy milk"})
    assert resp.status_code != 429
