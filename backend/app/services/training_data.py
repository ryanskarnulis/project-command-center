from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AITrainingExample


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
    return example
