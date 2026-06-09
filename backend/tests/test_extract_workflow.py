import json

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.workflows import extract_tasks as workflow
from app.db.models import AITrainingExample, InboxItem, Task, TaskReviewStatus
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


def _make_inbox_item(db: Session, raw_text: str = "messy notes") -> InboxItem:
    item = InboxItem(raw_text=raw_text, input_hash="hash-" + raw_text)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_extract_happy_path(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps(_VALID_OUTPUT)
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)

    item = _make_inbox_item(db_session)
    tasks = workflow.extract_tasks(db_session, item)

    assert len(tasks) == 2
    assert all(t.review_status == TaskReviewStatus.candidate for t in tasks)
    assert all(t.project_id is None for t in tasks)
    assert all(t.inbox_item_id == item.id for t in tasks)

    first = tasks[0]
    assert first.title == "Email the budget to Sarah"
    assert first.confidence == 0.9
    assert first.assignee_hint == "Sarah"

    # Inbox metadata persisted from the validated output.
    db_session.refresh(item)
    assert item.summary == "Two follow-ups from the standup."
    assert item.project_hint == "Firewall"
    assert item.needs_review is False
    assert item.processed_at is not None
    assert item.model_output_json == raw
    assert item.model_name == gateway.get_profile("task_extraction").model


def test_extract_rolls_back_candidates_if_metadata_commit_fails(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps(_VALID_OUTPUT)
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)
    original_create_task = workflow.create_task

    def create_then_fail(*args: object, **kwargs: object) -> Task:
        original_create_task(*args, **kwargs)
        raise RuntimeError("metadata write failed")

    monkeypatch.setattr(workflow, "create_task", create_then_fail)

    item = _make_inbox_item(db_session)
    item_id = item.id
    with pytest.raises(RuntimeError, match="metadata write failed"):
        workflow.extract_tasks(db_session, item)

    db_session.expire_all()
    saved_item = db_session.get(InboxItem, item_id)
    assert saved_item is not None
    assert saved_item.processed_at is None
    assert saved_item.model_output_json is None
    assert len(workflow._existing_candidates(db_session, item_id)) == 0


def test_extract_is_idempotent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps(_VALID_OUTPUT)
    calls = 0

    def fake_complete(**_: object) -> str:
        nonlocal calls
        calls += 1
        return raw

    monkeypatch.setattr(gateway, "complete", fake_complete)

    item = _make_inbox_item(db_session)
    first = workflow.extract_tasks(db_session, item)
    second = workflow.extract_tasks(db_session, item)

    assert calls == 1  # second run must not call the model again
    assert [t.id for t in first] == [t.id for t in second]


def test_extract_validation_failure_records_training_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = '{"summary": "oops", "tasks": "not a list", "needs_review": true}'
    monkeypatch.setattr(gateway, "complete", lambda **_: bad)

    item = _make_inbox_item(db_session, raw_text="bad extraction")

    with pytest.raises(ValidationError):
        workflow.extract_tasks(db_session, item)

    examples = (
        db_session.execute(active(AITrainingExample)).scalars().all()
    )
    assert len(examples) == 1
    example = examples[0]
    assert example.task_name == "task_extraction"
    assert example.input_text == "bad extraction"
    assert example.model_output_json == bad
    assert example.accepted is False

    # Failure must not leave a half-processed item or stray candidates.
    db_session.refresh(item)
    assert item.processed_at is None
    assert len(workflow._existing_candidates(db_session, item.id)) == 0
