from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

import structlog
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.schemas import ExtractionInput, ExtractionOutput
from app.db.models import InboxItem, Task, TaskStatus
from app.services.common import active
from app.services.tasks import create_task
from app.services.training_data import record_example

logger = structlog.get_logger(__name__)

_PROFILE = "task_extraction"


def _existing_candidates(db: Session, inbox_item_id: int) -> Sequence[Task]:
    return (
        db.execute(
            active(Task)
            .where(Task.inbox_item_id == inbox_item_id)
            .order_by(Task.id)
        )
        .scalars()
        .all()
    )


def extract_tasks(db: Session, inbox_item: InboxItem) -> Sequence[Task]:
    """Run the task-extraction workflow for one inbox item.

    Idempotent: if the item was already processed (or already has candidates
    from a partially-completed prior run), the existing candidates are returned
    without calling the model again. Otherwise: build the input, call the model
    through the gateway, validate the output with Pydantic, and create candidate
    tasks. On a validation failure the raw output is logged, saved as a training
    example, and the error is surfaced — never best-effort parsed into an empty
    list.
    """
    existing = _existing_candidates(db, inbox_item.id)
    if inbox_item.processed_at is not None or existing:
        logger.info(
            "extraction_skipped_idempotent",
            inbox_item_id=inbox_item.id,
            candidate_count=len(existing),
        )
        return existing

    logger.info("extraction_started", inbox_item_id=inbox_item.id)
    user_content = ExtractionInput(
        raw_text=inbox_item.raw_text, today=date.today()
    ).to_user_content()
    raw = gateway.complete(
        profile_name=_PROFILE,
        user_content=user_content,
        json_schema=ExtractionOutput.model_json_schema(),
    )

    model_name = gateway.get_profile(_PROFILE).model
    try:
        result = ExtractionOutput.model_validate_json(raw)
    except ValidationError as exc:
        logger.error(
            "extraction_validation_failed",
            inbox_item_id=inbox_item.id,
            raw_output=raw,
            error=str(exc),
        )
        record_example(
            db,
            task_name=_PROFILE,
            input_text=inbox_item.raw_text,
            model_output_json=raw,
            model_profile=_PROFILE,
            model_name=model_name,
        )
        raise

    tasks = [
        create_task(
            db,
            project_id=None,
            title=task.title,
            description=task.description,
            status=TaskStatus.candidate,
            priority=task.priority,
            due_date=task.due_date,
            inbox_item_id=inbox_item.id,
            confidence=task.confidence,
            assignee_hint=task.assignee_hint,
        )
        for task in result.tasks
    ]

    inbox_item.summary = result.summary
    inbox_item.project_hint = result.project_hint
    inbox_item.needs_review = result.needs_review
    inbox_item.model_output_json = raw
    inbox_item.model_name = model_name
    inbox_item.processed_at = datetime.now(UTC)
    db.commit()
    db.refresh(inbox_item)

    logger.info(
        "extraction_completed",
        inbox_item_id=inbox_item.id,
        candidate_count=len(tasks),
        needs_review=result.needs_review,
    )
    return tasks
