import json
import logging

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


def test_review_twice_conflicts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    inbox_id = client.post("/api/inbox", json={"raw_text": "notes to review"}).json()[
        "id"
    ]
    candidates = client.post(f"/api/inbox/{inbox_id}/process").json()
    decisions = {
        "decisions": [{"task_id": c["id"], "action": "accept"} for c in candidates]
    }

    first = client.post(f"/api/inbox/{inbox_id}/review", json=decisions)
    assert first.status_code == 200

    # Re-reviewing the same item is blocked (no duplicate training row).
    second = client.post(f"/api/inbox/{inbox_id}/review", json=decisions)
    assert second.status_code == 409

    examples = client.get(f"/api/inbox/{inbox_id}").json()
    assert examples["reviewed_at"] is not None


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


def test_process_malformed_extraction_422_and_training_row(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Model returns JSON that fails Pydantic validation (tasks must be a list).
    bad = '{"summary": "oops", "tasks": "not a list", "needs_review": true}'
    monkeypatch.setattr(gateway, "complete", lambda **_: bad)

    inbox_id = client.post("/api/inbox", json={"raw_text": "bad notes"}).json()["id"]

    # The error is surfaced as a 422 — not a silent empty candidate list.
    resp = client.post(f"/api/inbox/{inbox_id}/process")
    assert resp.status_code == 422

    # A failure training row was captured with the full raw model output.
    examples = db_session.execute(active(AITrainingExample)).scalars().all()
    assert len(examples) == 1
    assert examples[0].accepted is False
    assert examples[0].model_output_json == bad
    assert examples[0].input_text == "bad notes"


class _CaptureHandler(logging.Handler):
    """Records emitted log records so tests can assert on bound context."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_request_id_propagates_through_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """request_id bound by the middleware reaches every log line in the request,
    including the threadpool-run sync handler and the workflow it calls."""
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    handler = _CaptureHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        created = client.post("/api/inbox", json={"raw_text": "trace me"})
        create_rid = created.headers["X-Request-ID"]
        inbox_id = created.json()["id"]

        processed = client.post(f"/api/inbox/{inbox_id}/process")
        process_rid = processed.headers["X-Request-ID"]
    finally:
        root.removeHandler(handler)

    # Each request gets a distinct id, surfaced on the response header.
    assert create_rid and process_rid and create_rid != process_rid

    # merge_contextvars folds the bound request_id into every structlog event;
    # the event dict lives on record.msg.
    events = {
        record.msg["event"]: record.msg.get("request_id")
        for record in handler.records
        if isinstance(record.msg, dict) and "event" in record.msg
    }
    # Route log on the create request.
    assert events["inbox_created"] == create_rid
    # Workflow + route logs on the process request all carry the same id —
    # POST → extraction → validation → candidate creation → route.
    assert events["extraction_started"] == process_rid
    assert events["extraction_completed"] == process_rid
    assert events["inbox_processed"] == process_rid
