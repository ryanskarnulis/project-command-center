import json

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.workflows import break_down_task as workflow
from app.db.models import AITrainingExample, Task, TaskReviewStatus, TaskWorkflowStatus
from app.schemas.tasks import SubtaskDecision, SubtaskEdit
from app.services import breakdown as breakdown_service
from app.services.common import active
from app.services.tasks import create_task, list_subtasks

_VALID_OUTPUT = {
    "subtasks": [
        {
            "title": "Design the JWT claims",
            "description": "Pick algorithm and rotation.",
            "priority": "high",
            "estimated_minutes": 120,
            "confidence": 0.9,
        },
        {
            "title": "Add dual-read verification",
            "description": None,
            "priority": "medium",
            "estimated_minutes": None,
            "confidence": 0.7,
        },
    ],
    "needs_review": False,
}


def _make_task(db: Session, title: str = "Migrate auth to JWT") -> Task:
    return create_task(db, project_id=None, title=title, description="do it safely")


def test_break_down_happy_path(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps(_VALID_OUTPUT)
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)

    parent = _make_task(db_session)
    subtasks = workflow.break_down_task(db_session, parent)

    assert len(subtasks) == 2
    assert all(s.review_status == TaskReviewStatus.candidate for s in subtasks)
    assert all(s.parent_task_id == parent.id for s in subtasks)
    # Project inherited from the parent (General fallback, both None here).
    assert all(s.project_id == parent.project_id for s in subtasks)
    assert subtasks[0].title == "Design the JWT claims"
    assert subtasks[0].estimated_minutes == 120

    db_session.refresh(parent)
    assert parent.breakdown_output_json == raw


def test_break_down_is_idempotent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps(_VALID_OUTPUT)
    calls = 0

    def fake_complete(**_: object) -> str:
        nonlocal calls
        calls += 1
        return raw

    monkeypatch.setattr(gateway, "complete", fake_complete)

    parent = _make_task(db_session)
    first = workflow.break_down_task(db_session, parent)
    second = workflow.break_down_task(db_session, parent)

    assert calls == 1
    assert [s.id for s in first] == [s.id for s in second]


def test_break_down_validation_failure_records_training_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = '{"subtasks": "not a list", "needs_review": true}'
    monkeypatch.setattr(gateway, "complete", lambda **_: bad)

    parent = _make_task(db_session)
    with pytest.raises(ValidationError):
        workflow.break_down_task(db_session, parent)

    examples = db_session.execute(active(AITrainingExample)).scalars().all()
    assert len(examples) == 1
    assert examples[0].task_name == "break_down_task"
    assert examples[0].model_output_json == bad
    assert examples[0].accepted is False

    db_session.refresh(parent)
    assert parent.breakdown_output_json is None
    assert len(list_subtasks(db_session, parent.id)) == 0


def test_review_breakdown_approve_dismiss_and_capture(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps(_VALID_OUTPUT)
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)

    parent = _make_task(db_session)
    subtasks = workflow.break_down_task(db_session, parent)
    approve_id, dismiss_id = subtasks[0].id, subtasks[1].id

    result = breakdown_service.review_breakdown(
        db_session,
        parent,
        [
            SubtaskDecision(
                task_id=approve_id,
                action="approve",
                edits=SubtaskEdit(title="Design JWT claims and keys"),
            ),
            SubtaskDecision(task_id=dismiss_id, action="dismiss"),
        ],
    )

    assert result.approved == 1
    assert result.dismissed == 1
    assert result.finalized is True
    assert result.training_example_id is not None

    # Approved one is now an accepted subtask with the edit applied; dismissed one
    # is gone from the active subtask list.
    remaining = list_subtasks(db_session, parent.id)
    assert [s.id for s in remaining] == [approve_id]
    assert remaining[0].review_status == TaskReviewStatus.accepted
    assert remaining[0].title == "Design JWT claims and keys"
    assert remaining[0].workflow_status == TaskWorkflowStatus.open

    # Pending breakdown cleared; correction captured with full original output.
    db_session.refresh(parent)
    assert parent.breakdown_output_json is None
    example = db_session.execute(active(AITrainingExample)).scalars().one()
    assert example.task_name == "break_down_task"
    assert example.accepted is True
    corrected = json.loads(example.corrected_output_json or "{}")
    assert len(corrected["subtasks"]) == 1
    assert corrected["subtasks"][0]["title"] == "Design JWT claims and keys"


def test_review_breakdown_excludes_preexisting_manual_subtasks(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A subtask the user added by hand BEFORE running the breakdown is accepted,
    # but it isn't part of the model's suggestion. Recording it as corrected output
    # would poison the fine-tuning corpus (prime directive #4): the training row
    # must contain only THIS breakdown's own approved candidates.
    raw = json.dumps(_VALID_OUTPUT)
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)

    parent = _make_task(db_session)
    manual = create_task(
        db_session,
        project_id=parent.project_id,
        parent_task_id=parent.id,
        title="I wrote this myself",
    )
    db_session.commit()

    subtasks = workflow.break_down_task(db_session, parent)
    approve_id, dismiss_id = subtasks[0].id, subtasks[1].id

    result = breakdown_service.review_breakdown(
        db_session,
        parent,
        [
            SubtaskDecision(task_id=approve_id, action="approve"),
            SubtaskDecision(task_id=dismiss_id, action="dismiss"),
        ],
    )
    assert result.finalized is True

    # The manual subtask survives on the task (it was never a candidate)...
    remaining_ids = {s.id for s in list_subtasks(db_session, parent.id)}
    assert {manual.id, approve_id} <= remaining_ids

    # ...but the corrected output is scoped to the approved candidate only.
    example = db_session.execute(active(AITrainingExample)).scalars().one()
    assert example.accepted is True
    corrected = json.loads(example.corrected_output_json or "{}")
    titles = [s["title"] for s in corrected["subtasks"]]
    assert titles == ["Design the JWT claims"]


def test_review_breakdown_all_dismissed_records_rejection(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Dismissing every breakdown candidate is a rejection (accepted=False, empty
    # corrected output) even when the parent keeps pre-existing manual subtasks —
    # those aren't the model's output and must not flip the row to "accepted".
    raw = json.dumps(_VALID_OUTPUT)
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)

    parent = _make_task(db_session)
    create_task(
        db_session,
        project_id=parent.project_id,
        parent_task_id=parent.id,
        title="manual",
    )
    db_session.commit()

    subtasks = workflow.break_down_task(db_session, parent)
    result = breakdown_service.review_breakdown(
        db_session,
        parent,
        [SubtaskDecision(task_id=s.id, action="dismiss") for s in subtasks],
    )
    assert result.finalized is True

    example = db_session.execute(active(AITrainingExample)).scalars().one()
    assert example.accepted is False
    corrected = json.loads(example.corrected_output_json or "{}")
    assert corrected["subtasks"] == []


def test_review_breakdown_partial_does_not_finalize(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps(_VALID_OUTPUT)
    monkeypatch.setattr(gateway, "complete", lambda **_: raw)

    parent = _make_task(db_session)
    subtasks = workflow.break_down_task(db_session, parent)

    result = breakdown_service.review_breakdown(
        db_session,
        parent,
        [SubtaskDecision(task_id=subtasks[0].id, action="approve")],
    )
    assert result.finalized is False
    assert result.training_example_id is None

    db_session.refresh(parent)
    assert parent.breakdown_output_json == raw
    assert db_session.execute(active(AITrainingExample)).scalars().all() == []


def test_review_breakdown_without_pending_raises(db_session: Session) -> None:
    parent = _make_task(db_session)
    with pytest.raises(breakdown_service.AlreadyReviewedError):
        breakdown_service.review_breakdown(db_session, parent, [])
