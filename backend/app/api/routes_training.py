from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.models import AITrainingExample
from app.db.session import get_db
from app.schemas.training import TrainingExampleRead, TrainingStatsRead
from app.services import training_data

logger = structlog.get_logger(__name__)

# The training corpus is the app's core output (CLAUDE.md prime directive #4).
# These are read-only views over it — public like the dashboard reads, no auth.
router = APIRouter(prefix="/training-examples", tags=["training"])


@router.get("/stats", response_model=TrainingStatsRead)
def get_stats(db: Session = Depends(get_db)) -> TrainingStatsRead:
    """Corpus totals + per-task breakdown + progress toward the fine-tune goal."""
    total, accepted, by_task = training_data.example_stats(db)
    return TrainingStatsRead(total=total, accepted=accepted, by_task=by_task)


@router.get("", response_model=list[TrainingExampleRead])
def list_examples(
    task_name: str | None = Query(default=None),
    accepted: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Sequence[AITrainingExample]:
    """Training examples, newest-first, with optional task/accepted filters."""
    return training_data.list_examples(
        db, task_name=task_name, accepted=accepted, limit=limit, offset=offset
    )
