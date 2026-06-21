from __future__ import annotations

import json
from collections.abc import Sequence

import structlog
from sqlalchemy.orm import Session

from app.ai import gateway
from app.ai.schemas import BreakdownInput
from app.db.models import Task, TaskReviewStatus
from app.schemas.tasks import BreakdownReviewResult, SubtaskDecision
from app.services.tasks import list_subtasks, soft_delete_task
from app.services.training_data import record_example

logger = structlog.get_logger(__name__)

_PROFILE = "break_down_task"


class AlreadyReviewedError(Exception):
    """Raised when a task with no pending breakdown is reviewed for one.

    A breakdown is reviewed exactly once: re-reviewing would append a second,
    contradictory ``ai_training_examples`` row for the same input (prime
    directive #4). The pending state is ``parent.breakdown_output_json``; once
    cleared there is nothing to review.
    """


def _corrected_subtask(task: Task) -> dict[str, object]:
    """One accepted subtask as a ``BreakdownSubtask``-shaped dict for training."""
    return {
        "title": task.title,
        "description": task.description,
        "priority": task.priority.value,
        "estimated_minutes": task.estimated_minutes,
        "confidence": task.confidence,
    }


def review_breakdown(
    db: Session,
    parent: Task,
    decisions: Sequence[SubtaskDecision],
) -> BreakdownReviewResult:
    """Apply approve/dismiss decisions to a task's suggested subtasks.

    Approving flips a candidate child to ``accepted`` (applying any edits);
    dismissing soft-deletes it so it leaves the subtask list (recoverable in
    trash). Once no candidate children remain, the breakdown is finalized: one
    ``ai_training_examples`` row is written with the full original input, the full
    original model output, and the full corrected output (the accepted/edited
    subtasks), then ``breakdown_output_json`` is cleared.

    Raises ``AlreadyReviewedError`` if the parent has no pending breakdown, or
    ``ValueError`` if a decision references a task that is not a live candidate
    child of ``parent``.
    """
    if parent.breakdown_output_json is None:
        raise AlreadyReviewedError(
            f"task {parent.id} has no breakdown awaiting review"
        )

    try:
        candidates = {
            t.id: t
            for t in list_subtasks(db, parent.id)
            if t.review_status == TaskReviewStatus.candidate
        }

        approved = 0
        dismissed = 0
        for decision in decisions:
            task = candidates.get(decision.task_id)
            if task is None:
                raise ValueError(
                    f"task {decision.task_id} is not a candidate subtask of task {parent.id}"
                )
            if decision.action == "approve":
                edits = (
                    decision.edits.model_dump(exclude_unset=True)
                    if decision.edits is not None
                    else {}
                )
                for key, value in edits.items():
                    setattr(task, key, value)
                task.review_status = TaskReviewStatus.accepted
                approved += 1
            else:
                soft_delete_task(db, task)
                dismissed += 1

        db.flush()

        remaining = [
            t
            for t in list_subtasks(db, parent.id)
            if t.review_status == TaskReviewStatus.candidate
        ]
        finalized = not remaining
        training_example_id: int | None = None

        if finalized:
            accepted = [
                t
                for t in list_subtasks(db, parent.id)
                if t.review_status == TaskReviewStatus.accepted
            ]
            corrected = {
                "subtasks": [_corrected_subtask(t) for t in accepted],
                "needs_review": False,
            }
            input_text = BreakdownInput(
                title=parent.title, description=parent.description
            ).to_user_content()
            example = record_example(
                db,
                task_name=_PROFILE,
                input_text=input_text,
                model_output_json=parent.breakdown_output_json or "",
                corrected_output_json=json.dumps(corrected),
                accepted=bool(accepted),
                model_profile=_PROFILE,
                model_name=gateway.get_profile(_PROFILE).model,
            )
            training_example_id = example.id
            parent.breakdown_output_json = None

        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info(
        "breakdown_reviewed",
        task_id=parent.id,
        approved=approved,
        dismissed=dismissed,
        finalized=finalized,
        training_example_id=training_example_id,
    )
    return BreakdownReviewResult(
        approved=approved,
        dismissed=dismissed,
        finalized=finalized,
        training_example_id=training_example_id,
    )
