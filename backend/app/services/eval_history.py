from __future__ import annotations

from collections.abc import Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EvalRun

logger = structlog.get_logger(__name__)


def record_run(db: Session, *, suite: str, passed: int, total: int) -> EvalRun:
    """Persist one eval-suite run. Caller owns the transaction (commits)."""
    run = EvalRun(suite=suite, passed=passed, total=total)
    db.add(run)
    db.flush()
    db.refresh(run)
    logger.info(
        "eval_run_recorded", run_id=run.id, suite=suite, passed=passed, total=total
    )
    return run


def list_runs(
    db: Session, *, suite: str | None = None, limit: int = 50
) -> Sequence[EvalRun]:
    """Return eval runs newest-first, optionally filtered by suite."""
    stmt = select(EvalRun)
    if suite is not None:
        stmt = stmt.where(EvalRun.suite == suite)
    stmt = stmt.order_by(EvalRun.id.desc()).limit(limit)
    return db.execute(stmt).scalars().all()
