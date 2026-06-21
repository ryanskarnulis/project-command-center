from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import AITrainingExample
from app.services.common import active, deleted, hard_delete, restore, soft_delete

logger = structlog.get_logger(__name__)


def record_example(
    db: Session,
    *,
    task_name: str,
    input_text: str,
    model_output_json: str,
    model_profile: str,
    model_name: str,
    corrected_output_json: str | None = None,
    accepted: bool = False,
) -> AITrainingExample:
    """Persist one fine-tuning example.

    Stores the full input and full model output, never a diff — the diff alone
    is useless for training later. On the extraction failure path
    ``model_output_json`` is the raw (possibly invalid-JSON) model string; the
    column is plain text, so that is fine and intentional. Caller owns the
    transaction boundary; this helper only stages and flushes the row.
    """
    example = AITrainingExample(
        task_name=task_name,
        input_text=input_text,
        model_output_json=model_output_json,
        corrected_output_json=corrected_output_json,
        accepted=accepted,
        model_profile=model_profile,
        model_name=model_name,
    )
    db.add(example)
    db.flush()
    db.refresh(example)
    logger.info(
        "training_example_recorded",
        example_id=example.id,
        task_name=task_name,
        accepted=accepted,
        model_profile=model_profile,
        model_name=model_name,
        is_failure_case=corrected_output_json is None and not accepted,
    )
    return example


TrainingStatus = Literal["corrected", "accepted", "failure"]


def list_examples(
    db: Session,
    *,
    task_name: str | None = None,
    status: TrainingStatus | None = None,
    model_profile: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[AITrainingExample]:
    """Return active training examples, newest-first, with optional filters.

    Soft-deleted rows are excluded (the corpus is accounting data, so rows are
    never hard-deleted — but a soft-deleted row should not count toward the
    fine-tuning corpus or show in the viewer).

    ``status`` mirrors the frontend's three-way taxonomy (a correction outranks
    the accepted flag): ``corrected`` = has a correction; ``accepted`` = accepted
    with no correction; ``failure`` = neither (an extraction/validation failure).

    ``search`` is a case-insensitive substring match over ``input_text`` and
    ``model_output_json``. It runs server-side (not over the loaded page) so it
    stays correct under pagination. Substring semantics only — a ``%`` or ``_``
    in the term is treated literally by ILIKE wildcards, which is acceptable for
    this local corpus-inspection view.
    """
    stmt = active(AITrainingExample)
    if task_name is not None:
        stmt = stmt.where(AITrainingExample.task_name == task_name)
    if model_profile is not None:
        stmt = stmt.where(AITrainingExample.model_profile == model_profile)
    if status == "corrected":
        stmt = stmt.where(AITrainingExample.corrected_output_json.is_not(None))
    elif status == "accepted":
        stmt = stmt.where(
            AITrainingExample.accepted.is_(True),
            AITrainingExample.corrected_output_json.is_(None),
        )
    elif status == "failure":
        stmt = stmt.where(
            AITrainingExample.accepted.is_(False),
            AITrainingExample.corrected_output_json.is_(None),
        )
    if search is not None and search.strip():
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                AITrainingExample.input_text.ilike(term),
                AITrainingExample.model_output_json.ilike(term),
            )
        )
        logger.info(
            "training_examples_searched",
            term=search.strip(),
            task_name=task_name,
            status=status,
        )
    stmt = stmt.order_by(AITrainingExample.id.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


def example_stats(
    db: Session,
) -> tuple[int, int, dict[str, dict[str, int]], list[str]]:
    """Return (total, accepted, by_task, profiles) over active training examples.

    ``by_task`` maps ``task_name -> {"count": N, "accepted": M}`` via a single
    ``GROUP BY`` query, mirroring the dashboard's grouped-aggregate approach. The
    inner dict is coerced to ``TaskStat`` at the schema boundary. ``profiles`` is
    the distinct, sorted list of ``model_profile`` values, used to populate the
    Training page's profile filter dropdown.
    """
    rows = db.execute(
        select(
            AITrainingExample.task_name,
            func.count(AITrainingExample.id),
            func.count().filter(AITrainingExample.accepted.is_(True)),
        )
        .where(AITrainingExample.deleted_at.is_(None))
        .group_by(AITrainingExample.task_name)
    ).all()

    by_task: dict[str, dict[str, int]] = {}
    total = 0
    accepted = 0
    for task_name, count, accepted_count in rows:
        by_task[task_name] = {"count": int(count), "accepted": int(accepted_count)}
        total += int(count)
        accepted += int(accepted_count)

    profiles = list(
        db.execute(
            select(AITrainingExample.model_profile)
            .where(AITrainingExample.deleted_at.is_(None))
            .distinct()
            .order_by(AITrainingExample.model_profile)
        )
        .scalars()
        .all()
    )
    return total, accepted, by_task, profiles


# --- Trash / restore / purge -----------------------------------------------
#
# Soft-deleting an example drops it from ``list_examples`` and ``example_stats``
# automatically (both already filter ``deleted_at IS NULL``), so a trashed row
# no longer counts toward the fine-tuning corpus. ``ai_training_examples`` is a
# leaf table (nothing FKs into it), so restore has no uniqueness conflict to
# guard and purge needs no cascade cleanup.


def get_example(db: Session, example_id: int) -> AITrainingExample | None:
    """Return an active (non-trashed) training example, or ``None``."""
    return db.execute(
        active(AITrainingExample).where(AITrainingExample.id == example_id)
    ).scalar_one_or_none()


def get_deleted_example(db: Session, example_id: int) -> AITrainingExample | None:
    """Return a soft-deleted (trashed) training example, or ``None``."""
    return db.execute(
        deleted(AITrainingExample).where(AITrainingExample.id == example_id)
    ).scalar_one_or_none()


def soft_delete_example(db: Session, example: AITrainingExample) -> None:
    """Move a training example to trash. Caller commits.

    The corpus is "accounting data" (CLAUDE.md prime directive #4), so the
    default is to keep every row. The user explicitly opted into pruning junk
    examples — but only via the same reversible trash → purge path used for every
    other entity, so this just hides the row until it is restored or purged.
    """
    soft_delete(example)
    db.flush()
    logger.info("training_example_deleted", example_id=example.id)


def list_deleted_examples(
    db: Session, *, limit: int = 50
) -> Sequence[AITrainingExample]:
    """Soft-deleted training examples, most-recently-deleted first (trash view)."""
    return (
        db.execute(
            deleted(AITrainingExample)
            .order_by(AITrainingExample.deleted_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def restore_example(db: Session, example: AITrainingExample) -> AITrainingExample:
    """Un-trash a training example (it rejoins the corpus). Caller commits."""
    restore(example)
    db.flush()
    db.refresh(example)
    logger.info("training_example_restored", example_id=example.id)
    return example


def purge_example(db: Session, example: AITrainingExample) -> None:
    """Permanently delete a trashed training example. Caller commits.

    This is the one true delete for the corpus — a user-approved exception to the
    "never hard-delete training data" rule, confined (by ``hard_delete``'s guard)
    to rows already in trash. Leaf table, so no FK cleanup is needed first.
    """
    example_id = example.id  # captured before the row is expired by the delete
    hard_delete(db, example)
    logger.info("training_example_purged", example_id=example_id)
