from __future__ import annotations

from collections.abc import Sequence

import structlog
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.schemas import BreakdownInput, BreakdownOutput
from app.db.models import Task, TaskReviewStatus
from app.services.tasks import create_task, list_subtasks
from app.services.training_data import record_example

logger = structlog.get_logger(__name__)

_PROFILE = "break_down_task"


def _existing_candidate_subtasks(db: Session, task_id: int) -> list[Task]:
    """Active direct children of a task that are still awaiting review."""
    return [
        child
        for child in list_subtasks(db, task_id)
        if child.review_status == TaskReviewStatus.candidate
    ]


def break_down_task(db: Session, task: Task) -> Sequence[Task]:
    """Suggest subtasks for one task via the model, as candidate children.

    Idempotent: if this task already has candidate children (from a prior
    breakdown) or a pending ``breakdown_output_json``, the existing candidates are
    returned without calling the model again. Otherwise: build the input, call the
    model through the gateway, validate with Pydantic, and create candidate
    subtasks parented to this task (the project is inherited from the parent). On a
    validation failure the raw output is logged, saved as a training example, and
    the error is surfaced — never best-effort parsed (prime directive #3).
    """
    existing = _existing_candidate_subtasks(db, task.id)
    if existing or task.breakdown_output_json is not None:
        logger.info(
            "breakdown_skipped_idempotent",
            task_id=task.id,
            candidate_count=len(existing),
        )
        return existing

    logger.info("breakdown_started", task_id=task.id)
    user_content = BreakdownInput(
        title=task.title, description=task.description
    ).to_user_content()
    raw = gateway.complete(
        profile_name=_PROFILE,
        user_content=user_content,
        json_schema=BreakdownOutput.model_json_schema(),
    )

    model_name = gateway.get_profile(_PROFILE).model
    input_text = BreakdownInput(
        title=task.title, description=task.description
    ).to_user_content()
    try:
        result = BreakdownOutput.model_validate_json(raw)
    except ValidationError as exc:
        logger.error(
            "breakdown_validation_failed",
            task_id=task.id,
            raw_output=raw,
            error=str(exc),
        )
        try:
            record_example(
                db,
                task_name=_PROFILE,
                input_text=input_text,
                model_output_json=raw,
                model_profile=_PROFILE,
                model_name=model_name,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        raise

    try:
        subtasks = [
            create_task(
                db,
                project_id=None,  # inherits the parent's project
                parent_task_id=task.id,
                title=sub.title,
                description=sub.description,
                review_status=TaskReviewStatus.candidate,
                priority=sub.priority,
                estimated_minutes=sub.estimated_minutes,
                confidence=sub.confidence,
            )
            for sub in result.subtasks
        ]
        task.breakdown_output_json = raw
        db.commit()
        db.refresh(task)
        for sub in subtasks:
            db.refresh(sub)
    except Exception:
        db.rollback()
        raise

    logger.info(
        "breakdown_completed",
        task_id=task.id,
        candidate_count=len(subtasks),
        needs_review=result.needs_review,
    )
    return subtasks
