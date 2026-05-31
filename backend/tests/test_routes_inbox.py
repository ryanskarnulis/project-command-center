import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai import gateway
from app.db.models import AITrainingExample, TaskStatus
from app.services.common import active

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


def test_inbox_process_review_e2e(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    # Create.
    created = client.post("/api/inbox", json={"raw_text": "messy standup notes"})
    assert created.status_code == 201
    inbox_id = created.json()["id"]

    # Idempotent re-create → same id, no duplicate.
    again = client.post("/api/inbox", json={"raw_text": "messy standup notes"})
    assert again.status_code == 201
    assert again.json()["id"] == inbox_id

    # Process → two candidates.
    processed = client.post(f"/api/inbox/{inbox_id}/process")
    assert processed.status_code == 200
    candidates = processed.json()
    assert len(candidates) == 2
    assert all(c["status"] == TaskStatus.candidate for c in candidates)
    accept_id, reject_id = candidates[0]["id"], candidates[1]["id"]

    # Review: accept first (with an edit), reject second.
    review = client.post(
        f"/api/inbox/{inbox_id}/review",
        json={
            "decisions": [
                {
                    "task_id": accept_id,
                    "action": "accept",
                    "edits": {"title": "Email Q2 budget to Sarah"},
                },
                {"task_id": reject_id, "action": "reject"},
            ]
        },
    )
    assert review.status_code == 200
    result = review.json()
    assert result["accepted"] == 1
    assert result["rejected"] == 1

    # Task statuses persisted, edit applied.
    candidates_after = client.get(f"/api/inbox/{inbox_id}/candidates").json()
    by_id = {c["id"]: c for c in candidates_after}
    assert by_id[accept_id]["status"] == TaskStatus.accepted
    assert by_id[accept_id]["title"] == "Email Q2 budget to Sarah"
    assert by_id[reject_id]["status"] == TaskStatus.rejected

    # Exactly one training row; corrected output holds only the accepted/edited task.
    examples = db_session.execute(active(AITrainingExample)).scalars().all()
    assert len(examples) == 1
    example = examples[0]
    assert example.id == result["training_example_id"]
    assert example.task_name == "task_extraction"
    assert example.input_text == "messy standup notes"
    assert example.accepted is True
    corrected = json.loads(example.corrected_output_json)
    assert corrected["needs_review"] is False
    assert len(corrected["tasks"]) == 1
    assert corrected["tasks"][0]["title"] == "Email Q2 budget to Sarah"


def test_review_rejects_unknown_task_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    inbox_id = client.post("/api/inbox", json={"raw_text": "notes"}).json()["id"]
    client.post(f"/api/inbox/{inbox_id}/process")

    resp = client.post(
        f"/api/inbox/{inbox_id}/review",
        json={"decisions": [{"task_id": 99999, "action": "accept"}]},
    )
    assert resp.status_code == 400


def test_process_unknown_inbox_404(client: TestClient) -> None:
    assert client.post("/api/inbox/424242/process").status_code == 404
