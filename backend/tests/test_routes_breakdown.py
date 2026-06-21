import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai import gateway
from app.services.tasks import create_task

_VALID_OUTPUT = {
    "subtasks": [
        {
            "title": "Step one",
            "description": None,
            "priority": "medium",
            "estimated_minutes": 30,
            "confidence": 0.8,
        },
        {
            "title": "Step two",
            "description": None,
            "priority": "low",
            "estimated_minutes": None,
            "confidence": 0.6,
        },
    ],
    "needs_review": False,
}


def _make_task(db: Session) -> int:
    task = create_task(db, project_id=None, title="Big task")
    db.commit()
    return task.id


def test_break_down_route_returns_candidates(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))
    task_id = _make_task(db_session)

    resp = client.post(f"/api/tasks/{task_id}/break-down")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(row["parent_task_id"] == task_id for row in body)
    assert all(row["review_status"] == "candidate" for row in body)


def test_break_down_route_invalid_output_422(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: '{"subtasks": 1}')
    task_id = _make_task(db_session)

    resp = client.post(f"/api/tasks/{task_id}/break-down")
    assert resp.status_code == 422


def test_break_down_route_404_for_missing_task(client: TestClient) -> None:
    assert client.post("/api/tasks/999999/break-down").status_code == 404


def test_review_breakdown_route_finalizes(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))
    task_id = _make_task(db_session)

    candidates = client.post(f"/api/tasks/{task_id}/break-down").json()
    decisions = {
        "decisions": [
            {"task_id": candidates[0]["id"], "action": "approve"},
            {"task_id": candidates[1]["id"], "action": "dismiss"},
        ]
    }
    resp = client.post(f"/api/tasks/{task_id}/breakdown/review", json=decisions)
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] == 1
    assert body["dismissed"] == 1
    assert body["finalized"] is True
    assert body["training_example_id"] is not None

    # Reviewing again with nothing pending is a 409.
    again = client.post(f"/api/tasks/{task_id}/breakdown/review", json={"decisions": []})
    assert again.status_code == 409
