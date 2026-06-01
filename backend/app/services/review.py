from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from app.ai import gateway
from app.db.models import AITrainingExample, InboxItem, Task, TaskStatus
from app.schemas.inbox import ReviewDecision, ReviewResult
from app.services import inbox as inbox_service
from app.services import projects as projects_service
from app.services.training_data import record_example

logger = structlog.get_logger(__name__)

_PROFILE = "task_extraction"
_MATCH_PROFILE = "project_matching"


def _resolve_project_id(
    db: Session, project_id: int | None, *, explicit: bool
) -> int | None:
    """Validate the project a task is being accepted into.

    ``None`` stays ``None``. An explicit edit to a non-existent project is a user
    error (``ValueError`` → 400). A stale *suggestion* (project soft-deleted
    between matching and review) is dropped silently — the task is still accepted,
    just unfiled.
    """
    if project_id is None:
        return None
    if projects_service.get_project(db, project_id) is None:
        if explicit:
            raise ValueError(f"project {project_id} does not exist")
        return None
    return project_id


def _modal_project_id(tasks: Sequence[Task]) -> int | None:
    """The project_id the accepted tasks mostly landed in (first wins on a tie)."""
    counts = Counter(task.project_id for task in tasks)
    return counts.most_common(1)[0][0]


class AlreadyReviewedError(Exception):
    """Raised when an already-reviewed inbox item is reviewed again.

    Re-reviewing would re-flip task statuses and append a second, contradictory
    ``ai_training_examples`` row for the same input — polluting the training set
    (prime directive #4). One inbox item gets exactly one review.
    """


def _corrected_task(task: Task) -> dict[str, object]:
    """One accepted task as an ``ExtractedTask``-shaped dict for training data."""
    return {
        "title": task.title,
        "description": task.description,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "priority": task.priority.value,
        "assignee_hint": task.assignee_hint,
        "confidence": task.confidence,
    }


def review_inbox(
    db: Session,
    item: InboxItem,
    decisions: Sequence[ReviewDecision],
) -> ReviewResult:
    """Apply review decisions and capture exactly one training example.

    Each decision flips a candidate task to ``accepted`` (applying any edits) or
    ``rejected``. The corrected output written to ``ai_training_examples`` is the
    full ``ExtractionOutput`` the model *should* have produced: the accepted (and
    edited) tasks only, with rejected tasks dropped. We store the full original
    input, the full original model output, and this full corrected output — never
    a diff (prime directive #4).

    Raises ``ValueError`` if a decision references a task that is not an active
    candidate of this inbox item, or ``AlreadyReviewedError`` if the item was
    already reviewed.
    """
    if item.reviewed_at is not None:
        raise AlreadyReviewedError(
            f"inbox item {item.id} was already reviewed at {item.reviewed_at.isoformat()}"
        )

    candidates = {t.id: t for t in inbox_service.list_candidates(db, item.id)}

    accepted: list[Task] = []
    rejected_count = 0
    for decision in decisions:
        task = candidates.get(decision.task_id)
        if task is None:
            raise ValueError(
                f"task {decision.task_id} is not a candidate of inbox item {item.id}"
            )
        if decision.action == "accept":
            task.status = TaskStatus.accepted
            edits = (
                decision.edits.model_dump(exclude_unset=True)
                if decision.edits is not None
                else {}
            )
            # project_id is resolved separately (guarded); the rest are plain sets.
            explicit_project = "project_id" in edits
            chosen_project = edits.pop("project_id", item.suggested_project_id)
            for key, value in edits.items():
                setattr(task, key, value)
            task.project_id = _resolve_project_id(
                db, chosen_project, explicit=explicit_project
            )
            accepted.append(task)
        else:
            task.status = TaskStatus.rejected
            rejected_count += 1

    item.reviewed_at = datetime.now(UTC)
    db.commit()
    for task in accepted:
        db.refresh(task)

    corrected = {
        "summary": item.summary,
        "project_hint": item.project_hint,
        "tasks": [_corrected_task(t) for t in accepted],
        "needs_review": False,
    }
    example: AITrainingExample = record_example(
        db,
        task_name=_PROFILE,
        input_text=item.raw_text,
        model_output_json=item.model_output_json or "",
        corrected_output_json=json.dumps(corrected),
        accepted=bool(accepted),
        model_profile=_PROFILE,
        model_name=item.model_name or gateway.get_profile(_PROFILE).model,
    )

    # Capture a project_matching correction too — but only when the *model* made
    # the suggestion (match_output_json set; deterministic alias hits have no
    # model output to train on) and at least one task was accepted to file. The
    # corrected output is where the user actually filed the accepted tasks.
    match_example_id: int | None = None
    if item.match_output_json is not None and accepted:
        corrected_project_id = _modal_project_id(accepted)
        match_example = record_example(
            db,
            task_name=_MATCH_PROFILE,
            input_text=item.match_input_text or "",
            model_output_json=item.match_output_json,
            corrected_output_json=json.dumps({"project_id": corrected_project_id}),
            accepted=item.suggested_project_id == corrected_project_id,
            model_profile=_MATCH_PROFILE,
            model_name=item.match_model_name or gateway.get_profile(_MATCH_PROFILE).model,
        )
        match_example_id = match_example.id

    logger.info(
        "inbox_review_recorded",
        inbox_item_id=item.id,
        accepted=len(accepted),
        rejected=rejected_count,
        training_example_id=example.id,
        match_training_example_id=match_example_id,
    )
    return ReviewResult(
        accepted=len(accepted),
        rejected=rejected_count,
        training_example_id=example.id,
        match_training_example_id=match_example_id,
    )
