from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from app.ai import gateway
from app.db.models import AITrainingExample, InboxItem, Task, TaskStatus
from app.schemas.inbox import CandidateDecision, CandidateResult, ReviewDecision, ReviewResult
from app.services import activity as activity_service
from app.services import inbox as inbox_service
from app.services import projects as projects_service
from app.services.training_data import record_example

logger = structlog.get_logger(__name__)

_PROFILE = "task_extraction"
_MATCH_PROFILE = "project_matching"


def _resolve_project_id(
    db: Session, project_id: int | None, *, explicit: bool
) -> int:
    """Validate the project a task is being accepted into.

    Accepted tasks are always filed: ``None`` and stale non-explicit suggestions
    fall back to the protected ``General`` project. An explicit edit to a
    non-existent project is still a user error (``ValueError`` → 400).
    """
    default_project_id = projects_service.ensure_default_project_id(db)
    if project_id is None:
        return default_project_id
    if projects_service.get_project(db, project_id) is None:
        if explicit:
            raise ValueError(f"project {project_id} does not exist")
        return default_project_id
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

    try:
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
        db.flush()
        for task in accepted:
            db.refresh(task)

        # Activity feed: an accepted candidate is the task's first appearance in a
        # project, so log it as "created" (matching a directly-created task). Review
        # mutates tasks in bulk here rather than via tasks_service, so the logging
        # hook in that service doesn't fire — emit explicitly. Rejected tasks
        # produce nothing.
        for task in accepted:
            if task.project_id is not None:
                activity_service.record_event(
                    db,
                    project_id=task.project_id,
                    entity_type="task",
                    entity_id=task.id,
                    action="created",
                    summary=f'Task "{task.title}" created',
                )

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
                model_name=(
                    item.match_model_name or gateway.get_profile(_MATCH_PROFILE).model
                ),
            )
            match_example_id = match_example.id
        db.commit()
    except Exception:
        db.rollback()
        raise

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


def _finalize_inbox(
    db: Session,
    item: InboxItem,
) -> tuple[int, int | None]:
    """Set reviewed_at and write training rows once all candidates are decided.

    Returns ``(training_example_id, match_training_example_id | None)``.
    This is the same logic as ``review_inbox`` but driven by the per-candidate
    flow rather than a batch call.
    """
    accepted = [
        t for t in inbox_service.list_candidates(db, item.id)
        if t.status == TaskStatus.accepted
    ]
    item.reviewed_at = datetime.now(UTC)
    db.flush()

    for task in accepted:
        if task.project_id is not None:
            activity_service.record_event(
                db,
                project_id=task.project_id,
                entity_type="task",
                entity_id=task.id,
                action="created",
                summary=f'Task "{task.title}" created',
            )

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
            model_name=(
                item.match_model_name or gateway.get_profile(_MATCH_PROFILE).model
            ),
        )
        match_example_id = match_example.id

    return example.id, match_example_id


def decide_candidate(
    db: Session,
    item: InboxItem,
    task: Task,
    decision: CandidateDecision,
) -> CandidateResult:
    """Approve or dismiss a single candidate task.

    After the decision, if no candidates remain (status == 'candidate'), the
    inbox item is finalized: ``reviewed_at`` is set and the training examples are
    written (prime directive #4 — exactly one row per inbox item, after all
    decisions are in).

    Raises:
        ``AlreadyReviewedError`` — the inbox item was already finalized.
        ``ValueError`` — the task is not a live candidate of this item.
    """
    if item.reviewed_at is not None:
        raise AlreadyReviewedError(
            f"inbox item {item.id} was already reviewed at {item.reviewed_at.isoformat()}"
        )
    if task.inbox_item_id != item.id or task.status != TaskStatus.candidate:
        raise ValueError(
            f"task {task.id} is not a live candidate of inbox item {item.id}"
        )

    try:
        if decision.action == "approve":
            edits = (
                decision.edits.model_dump(exclude_unset=True)
                if decision.edits is not None
                else {}
            )
            explicit_project = "project_id" in edits
            chosen_project = edits.pop("project_id", item.suggested_project_id)
            for key, value in edits.items():
                setattr(task, key, value)
            task.project_id = _resolve_project_id(
                db, chosen_project, explicit=explicit_project
            )
            task.status = TaskStatus.accepted
        else:
            task.status = TaskStatus.rejected

        db.flush()
        db.refresh(task)

        # Check if any undecided candidates remain. If none, finalize.
        remaining = [
            t for t in inbox_service.list_candidates(db, item.id)
            if t.status == TaskStatus.candidate and t.id != task.id
        ]
        finalized = len(remaining) == 0
        training_example_id: int | None = None
        match_training_example_id: int | None = None

        if finalized:
            training_example_id, match_training_example_id = _finalize_inbox(db, item)

        db.commit()
    except Exception:
        db.rollback()
        raise

    action_str: str = "approved" if decision.action == "approve" else "dismissed"
    logger.info(
        "candidate_decided",
        inbox_item_id=item.id,
        task_id=task.id,
        action=action_str,
        finalized=finalized,
    )
    return CandidateResult(
        task_id=task.id,
        action=action_str,  # type: ignore[arg-type]
        finalized=finalized,
        training_example_id=training_example_id,
        match_training_example_id=match_training_example_id,
    )
