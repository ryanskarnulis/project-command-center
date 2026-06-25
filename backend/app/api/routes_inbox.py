from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.gateway import GatewayError
from app.ai.workflows import extract_tasks as extract_workflow
from app.ai.workflows import match_project as match_workflow
from app.api.rate_limit import rate_limit
from app.db.models import InboxItem, Task, TaskReviewStatus
from app.db.session import get_db
from app.schemas.inbox import (
    CandidateDecision,
    CandidateResult,
    InboxCreate,
    InboxRead,
    ReviewRequest,
    ReviewResult,
)
from app.schemas.tasks import TaskRead
from app.services import inbox as inbox_service
from app.services import review as review_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/inbox", tags=["inbox"])


def _get_inbox_or_404(db: Session, inbox_item_id: int) -> InboxItem:
    item = inbox_service.get_inbox_item(db, inbox_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Inbox item not found"
        )
    return item


@router.post("", response_model=InboxRead, status_code=status.HTTP_201_CREATED)
def create_inbox(data: InboxCreate, db: Session = Depends(get_db)) -> InboxItem:
    item = inbox_service.create_inbox_item(
        db, raw_text=data.raw_text, source=data.source
    )
    db.commit()
    db.refresh(item)
    logger.info("inbox_created", inbox_item_id=item.id, source=item.source)
    return item


@router.get("", response_model=list[InboxRead])
def list_inbox(db: Session = Depends(get_db)) -> Sequence[InboxItem]:
    return inbox_service.list_inbox_items(db)


@router.get("/pending", response_model=list[InboxRead])
def list_pending_inbox(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> Sequence[InboxItem]:
    return inbox_service.list_pending_review_items(db, limit=limit)


@router.get("/{inbox_item_id}", response_model=InboxRead)
def get_inbox(inbox_item_id: int, db: Session = Depends(get_db)) -> InboxItem:
    return _get_inbox_or_404(db, inbox_item_id)


@router.delete("/{inbox_item_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_inbox(inbox_item_id: int, db: Session = Depends(get_db)) -> None:
    item = _get_inbox_or_404(db, inbox_item_id)
    inbox_service.dismiss_inbox_item(db, item)
    db.commit()
    logger.info("inbox_dismissed", inbox_item_id=inbox_item_id)


@router.post("/{inbox_item_id}/restore", response_model=InboxRead)
def restore_inbox(inbox_item_id: int, db: Session = Depends(get_db)) -> InboxItem:
    item = inbox_service.get_deleted_inbox_item(db, inbox_item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No dismissed inbox item with that id",
        )
    try:
        restored = inbox_service.restore_inbox_item(db, item)
    except inbox_service.RestoreConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(restored)
    logger.info("inbox_restored", inbox_item_id=restored.id)
    return restored


@router.delete("/{inbox_item_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_inbox(inbox_item_id: int, db: Session = Depends(get_db)) -> None:
    item = inbox_service.get_deleted_inbox_item(db, inbox_item_id)
    if item is None:
        # Active item (exists, not dismissed) → 409; truly absent → 404.
        if inbox_service.get_inbox_item(db, inbox_item_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Inbox item is not in trash; dismiss it first",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No dismissed inbox item with that id",
        )
    inbox_service.purge_inbox_item(db, item)
    db.commit()
    logger.info("inbox_purged", inbox_item_id=inbox_item_id)


@router.post(
    "/{inbox_item_id}/process",
    response_model=list[TaskRead],
    dependencies=[
        Depends(
            rate_limit(
                "inbox_process", per_min_attr="rate_limit_inbox_process_per_min"
            )
        )
    ],
)
def process_inbox(
    inbox_item_id: int, db: Session = Depends(get_db)
) -> Sequence[Task]:
    item = _get_inbox_or_404(db, inbox_item_id)
    try:
        candidates = extract_workflow.extract_tasks(db, item)
    except ValidationError:
        # The workflow already logged the raw output and wrote a failure training
        # row; surface the error rather than returning a silent empty list.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="extraction validation failed",
        ) from None
    except GatewayError as exc:
        # Ollama unreachable / timeout: report an upstream failure, never a 500.
        logger.error("extraction_upstream_error", inbox_item_id=item.id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="extraction service unavailable — is Ollama running?",
        ) from exc

    # Project matching is enrichment, not the core product: a failure here (bad
    # model output, Ollama unreachable) must not lose the extracted tasks, so it
    # is best-effort and never fails /process.
    try:
        match_workflow.match_inbox_item(db, item)
    except Exception:  # noqa: BLE001 — matching is non-fatal enrichment
        logger.exception("match_failed", inbox_item_id=item.id)

    logger.info(
        "inbox_processed",
        inbox_item_id=item.id,
        candidate_count=len(candidates),
    )
    return candidates


@router.get("/{inbox_item_id}/candidates", response_model=list[TaskRead])
def list_candidates(
    inbox_item_id: int, db: Session = Depends(get_db)
) -> Sequence[Task]:
    """Still-undecided candidates for an inbox item.

    Decided tasks (accepted/rejected) are excluded so they don't reappear in the
    review queue after the user leaves and returns.
    """
    _get_inbox_or_404(db, inbox_item_id)
    return inbox_service.list_candidates(
        db, inbox_item_id, review_status=TaskReviewStatus.candidate
    )


@router.post("/{inbox_item_id}/candidates/{task_id}", response_model=CandidateResult)
def decide_candidate(
    inbox_item_id: int,
    task_id: int,
    data: CandidateDecision,
    db: Session = Depends(get_db),
) -> CandidateResult:
    """Approve or dismiss a single candidate task.

    Finalizes the inbox item (writes training data) once the last candidate is decided.
    """
    from app.services import tasks as tasks_service  # local to avoid circular import

    item = _get_inbox_or_404(db, inbox_item_id)
    task = tasks_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    try:
        result = review_service.decide_candidate(db, item, task, data)
    except review_service.AlreadyReviewedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return result


@router.post("/{inbox_item_id}/review", response_model=ReviewResult)
def review_inbox(
    inbox_item_id: int, data: ReviewRequest, db: Session = Depends(get_db)
) -> ReviewResult:
    item = _get_inbox_or_404(db, inbox_item_id)
    try:
        result = review_service.review_inbox(db, item, data.decisions)
    except review_service.AlreadyReviewedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except review_service.IncompleteReviewError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    logger.info(
        "inbox_reviewed",
        inbox_item_id=item.id,
        accepted=result.accepted,
        rejected=result.rejected,
        training_example_id=result.training_example_id,
    )
    return result
