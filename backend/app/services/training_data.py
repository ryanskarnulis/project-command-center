from __future__ import annotations

from collections.abc import Sequence

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AITrainingExample
from app.services.common import active

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


def list_examples(
    db: Session,
    *,
    task_name: str | None = None,
    accepted: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[AITrainingExample]:
    """Return active training examples, newest-first, with optional filters.

    Soft-deleted rows are excluded (the corpus is accounting data, so rows are
    never hard-deleted — but a soft-deleted row should not count toward the
    fine-tuning corpus or show in the viewer).
    """
    stmt = active(AITrainingExample)
    if task_name is not None:
        stmt = stmt.where(AITrainingExample.task_name == task_name)
    if accepted is not None:
        stmt = stmt.where(AITrainingExample.accepted == accepted)
    stmt = stmt.order_by(AITrainingExample.id.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


def example_stats(db: Session) -> tuple[int, int, dict[str, int]]:
    """Return (total, accepted, by_task) over active training examples.

    ``by_task`` maps ``task_name -> count`` via a single ``GROUP BY`` query,
    mirroring the dashboard's grouped-aggregate approach.
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

    by_task: dict[str, int] = {}
    total = 0
    accepted = 0
    for task_name, count, accepted_count in rows:
        by_task[task_name] = int(count)
        total += int(count)
        accepted += int(accepted_count)
    return total, accepted, by_task
