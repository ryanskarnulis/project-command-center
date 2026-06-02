import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai import gateway
from app.db.models import AITrainingExample, TaskStatus
from app.services import inbox as inbox_service
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


def test_create_inbox_strips_raw_text_and_rejects_blank(client: TestClient) -> None:
    created = client.post("/api/inbox", json={"raw_text": "  messy notes  "})
    assert created.status_code == 201
    body = created.json()
    assert body["raw_text"] == "messy notes"
    assert body["input_hash"] == inbox_service.hash_text("messy notes")

    again = client.post("/api/inbox", json={"raw_text": "messy notes"})
    assert again.status_code == 201
    assert again.json()["id"] == body["id"]

    blank = client.post("/api/inbox", json={"raw_text": "   "})
    assert blank.status_code == 422


def _fake_gateway(match_output: str):
    """A gateway.complete double: extraction returns _VALID_OUTPUT (project_hint
    "Firewall"), the project_matching call returns ``match_output``."""

    def fake(*, profile_name: str, **_: object) -> str:
        if profile_name == "project_matching":
            return match_output
        return json.dumps(_VALID_OUTPUT)

    return fake


def _match_rows(db: Session) -> list[AITrainingExample]:
    rows = db.execute(active(AITrainingExample)).scalars().all()
    return [r for r in rows if r.task_name == "project_matching"]


def test_review_inherits_ai_suggestion_and_captures_match(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Home Network" does not match the hint "Firewall" deterministically, so the
    # AI fallback runs; we make it suggest this project.
    pid = client.post("/api/projects", json={"name": "Home Network"}).json()["id"]
    monkeypatch.setattr(
        gateway,
        "complete",
        _fake_gateway(json.dumps({"project_id": pid, "confidence": 0.9})),
    )

    inbox_id = client.post("/api/inbox", json={"raw_text": "standup"}).json()["id"]
    candidates = client.post(f"/api/inbox/{inbox_id}/process").json()

    # The suggestion is exposed on the inbox item.
    assert client.get(f"/api/inbox/{inbox_id}").json()["suggested_project_id"] == pid

    # Accept both with no project edits → both inherit the suggestion.
    review = client.post(
        f"/api/inbox/{inbox_id}/review",
        json={"decisions": [{"task_id": c["id"], "action": "accept"} for c in candidates]},
    ).json()

    after = client.get(f"/api/inbox/{inbox_id}/candidates").json()
    assert all(c["project_id"] == pid for c in after)

    match_rows = _match_rows(db_session)
    assert len(match_rows) == 1
    assert match_rows[0].id == review["match_training_example_id"]
    assert match_rows[0].accepted is True  # kept the suggestion
    assert json.loads(match_rows[0].corrected_output_json) == {"project_id": pid}


def test_review_override_redirects_and_records_correction(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = client.post("/api/projects", json={"name": "Home Network"}).json()["id"]
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    monkeypatch.setattr(
        gateway, "complete", _fake_gateway(json.dumps({"project_id": pid, "confidence": 0.9}))
    )

    inbox_id = client.post("/api/inbox", json={"raw_text": "standup"}).json()["id"]
    candidates = client.post(f"/api/inbox/{inbox_id}/process").json()

    # Override every accepted task to a different project than the suggestion.
    client.post(
        f"/api/inbox/{inbox_id}/review",
        json={
            "decisions": [
                {"task_id": c["id"], "action": "accept", "edits": {"project_id": other}}
                for c in candidates
            ]
        },
    )

    after = client.get(f"/api/inbox/{inbox_id}/candidates").json()
    assert all(c["project_id"] == other for c in after)

    match_rows = _match_rows(db_session)
    assert len(match_rows) == 1
    assert match_rows[0].accepted is False  # suggestion was overridden
    assert json.loads(match_rows[0].corrected_output_json) == {"project_id": other}


def test_review_deterministic_suggestion_writes_no_match_row(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A project literally named "Firewall" matches the hint deterministically — no
    # model output, so there is nothing to capture as a match training example.
    pid = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]
    monkeypatch.setattr(gateway, "complete", _fake_gateway("unused"))

    inbox_id = client.post("/api/inbox", json={"raw_text": "standup"}).json()["id"]
    candidates = client.post(f"/api/inbox/{inbox_id}/process").json()
    assert client.get(f"/api/inbox/{inbox_id}").json()["suggested_project_id"] == pid

    client.post(
        f"/api/inbox/{inbox_id}/review",
        json={"decisions": [{"task_id": candidates[0]["id"], "action": "accept"}]},
    )

    after = {c["id"]: c for c in client.get(f"/api/inbox/{inbox_id}/candidates").json()}
    assert after[candidates[0]["id"]]["project_id"] == pid  # inherited deterministically
    assert _match_rows(db_session) == []


def test_review_override_to_missing_project_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post("/api/projects", json={"name": "Home Network"})
    monkeypatch.setattr(gateway, "complete", _fake_gateway(json.dumps({"project_id": 1, "confidence": 0.5})))

    inbox_id = client.post("/api/inbox", json={"raw_text": "standup"}).json()["id"]
    candidates = client.post(f"/api/inbox/{inbox_id}/process").json()

    resp = client.post(
        f"/api/inbox/{inbox_id}/review",
        json={
            "decisions": [
                {"task_id": candidates[0]["id"], "action": "accept", "edits": {"project_id": 9999}}
            ]
        },
    )
    assert resp.status_code == 400


def test_review_edit_strips_and_rejects_blank_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    inbox_id = client.post("/api/inbox", json={"raw_text": "notes to edit"}).json()["id"]
    candidates = client.post(f"/api/inbox/{inbox_id}/process").json()
    task_id = candidates[0]["id"]

    blank_title = client.post(
        f"/api/inbox/{inbox_id}/review",
        json={
            "decisions": [
                {
                    "task_id": task_id,
                    "action": "accept",
                    "edits": {"title": "   "},
                }
            ]
        },
    )
    assert blank_title.status_code == 422

    review = client.post(
        f"/api/inbox/{inbox_id}/review",
        json={
            "decisions": [
                {
                    "task_id": task_id,
                    "action": "accept",
                    "edits": {
                        "title": "  Fixed title  ",
                        "description": "   ",
                        "assignee_hint": "   ",
                    },
                }
            ]
        },
    )
    assert review.status_code == 200

    candidate = {
        candidate["id"]: candidate
        for candidate in client.get(f"/api/inbox/{inbox_id}/candidates").json()
    }[task_id]
    assert candidate["title"] == "Fixed title"
    assert candidate["description"] is None
    assert candidate["assignee_hint"] is None


def test_review_reject_all_writes_no_match_row(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = client.post("/api/projects", json={"name": "Home Network"}).json()["id"]
    monkeypatch.setattr(
        gateway, "complete", _fake_gateway(json.dumps({"project_id": pid, "confidence": 0.9}))
    )

    inbox_id = client.post("/api/inbox", json={"raw_text": "standup"}).json()["id"]
    candidates = client.post(f"/api/inbox/{inbox_id}/process").json()

    client.post(
        f"/api/inbox/{inbox_id}/review",
        json={"decisions": [{"task_id": c["id"], "action": "reject"} for c in candidates]},
    )
    # An AI suggestion existed, but nothing was accepted → no match signal.
    assert _match_rows(db_session) == []


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


def test_review_empty_decisions_dismisses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty-decision review marks the item reviewed (the 'dismiss' path used
    by the web UI for notes that extracted no tasks) and records one example."""
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    inbox_id = client.post("/api/inbox", json={"raw_text": "nothing to do here"}).json()[
        "id"
    ]
    client.post(f"/api/inbox/{inbox_id}/process")

    resp = client.post(f"/api/inbox/{inbox_id}/review", json={"decisions": []})
    assert resp.status_code == 200
    result = resp.json()
    assert result["accepted"] == 0
    assert result["rejected"] == 0

    # The item drops out of the pending set (it's now reviewed).
    assert client.get(f"/api/inbox/{inbox_id}").json()["reviewed_at"] is not None
    # Re-dismissing is still blocked.
    assert (
        client.post(f"/api/inbox/{inbox_id}/review", json={"decisions": []}).status_code
        == 409
    )


def test_list_pending_inbox_filters_orders_and_limits(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "complete", lambda **_: json.dumps(_VALID_OUTPUT))

    unprocessed_id = client.post("/api/inbox", json={"raw_text": "unprocessed"}).json()[
        "id"
    ]

    reviewed_id = client.post("/api/inbox", json={"raw_text": "reviewed"}).json()["id"]
    reviewed_candidates = client.post(f"/api/inbox/{reviewed_id}/process").json()
    client.post(
        f"/api/inbox/{reviewed_id}/review",
        json={
            "decisions": [
                {"task_id": candidate["id"], "action": "accept"}
                for candidate in reviewed_candidates
            ]
        },
    )

    older_pending_id = client.post(
        "/api/inbox", json={"raw_text": "older pending"}
    ).json()["id"]
    client.post(f"/api/inbox/{older_pending_id}/process")

    newer_pending_id = client.post(
        "/api/inbox", json={"raw_text": "newer pending"}
    ).json()["id"]
    client.post(f"/api/inbox/{newer_pending_id}/process")

    pending = client.get("/api/inbox/pending").json()
    assert [item["id"] for item in pending] == [newer_pending_id, older_pending_id]
    assert unprocessed_id not in {item["id"] for item in pending}
    assert reviewed_id not in {item["id"] for item in pending}

    limited = client.get("/api/inbox/pending?limit=1").json()
    assert [item["id"] for item in limited] == [newer_pending_id]


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
