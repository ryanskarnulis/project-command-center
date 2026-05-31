from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from app.ai import gateway
from app.db.models import AITrainingExample, InboxItem, Task, TaskStatus
from app.schemas.inbox import ReviewDecision, ReviewResult
from app.services import inbox as inbox_service
from app.services.training_data import record_example

logger = structlog.get_logger(__name__)

_PROFILE = "task_extraction"


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
            if decision.edits is not None:
                edits = decision.edits.model_dump(exclude_unset=True)
                for key, value in edits.items():
                    setattr(task, key, value)
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

    logger.info(
        "inbox_review_recorded",
        inbox_item_id=item.id,
        accepted=len(accepted),
        rejected=rejected_count,
        training_example_id=example.id,
    )
    return ReviewResult(
        accepted=len(accepted),
        rejected=rejected_count,
        training_example_id=example.id,
    )
