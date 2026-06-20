from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.models import AITrainingExample
from app.db.session import get_db
from app.schemas.training import TaskStat, TrainingExampleRead, TrainingStatsRead
from app.services import training_data

logger = structlog.get_logger(__name__)

# The training corpus is the app's core output (CLAUDE.md prime directive #4).
# These are read-only views over it — public like the dashboard reads, no auth.
router = APIRouter(prefix="/training-examples", tags=["training"])


@router.get("/stats", response_model=TrainingStatsRead)
def get_stats(db: Session = Depends(get_db)) -> TrainingStatsRead:
    """Corpus totals + per-task breakdown + progress toward the fine-tune goal."""
    total, accepted, by_task = training_data.example_stats(db)
    return TrainingStatsRead(
        total=total,
        accepted=accepted,
        by_task={
            task: TaskStat(count=stat["count"], accepted=stat["accepted"])
            for task, stat in by_task.items()
        },
    )


@router.get("", response_model=list[TrainingExampleRead])
def list_examples(
    task_name: str | None = Query(default=None),
    accepted: bool | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Sequence[AITrainingExample]:
    """Training examples, newest-first, with optional task/accepted/search filters.

    ``search`` matches a case-insensitive substring against the input text or the
    model output JSON (server-side, so it stays correct under pagination).
    """
    return training_data.list_examples(
        db,
        task_name=task_name,
        accepted=accepted,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.delete("/{example_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_example(example_id: int, db: Session = Depends(get_db)) -> None:
    """Soft-delete a training example (move it to trash; reversible)."""
    example = training_data.get_example(db, example_id)
    if example is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active training example with that id",
        )
    training_data.soft_delete_example(db, example)
    db.commit()


@router.post("/{example_id}/restore", response_model=TrainingExampleRead)
def restore_example(
    example_id: int, db: Session = Depends(get_db)
) -> AITrainingExample:
    """Restore a trashed training example back into the corpus."""
    example = training_data.get_deleted_example(db, example_id)
    if example is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No trashed training example with that id",
        )
    restored = training_data.restore_example(db, example)
    db.commit()
    db.refresh(restored)
    return restored


@router.delete("/{example_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_example(example_id: int, db: Session = Depends(get_db)) -> None:
    """Permanently delete a trashed training example (irreversible)."""
    example = training_data.get_deleted_example(db, example_id)
    if example is None:
        # Active example (exists, not trashed) → 409; truly absent → 404.
        if training_data.get_example(db, example_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Training example is not in trash; delete it first",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No trashed training example with that id",
        )
    training_data.purge_example(db, example)
    db.commit()
