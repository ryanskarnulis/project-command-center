from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.workflows import extract_tasks as extract_workflow
from app.db.models import InboxItem, Task
from app.db.session import get_db
from app.schemas.inbox import InboxCreate, InboxRead, ReviewRequest, ReviewResult
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
    logger.info("inbox_created", inbox_item_id=item.id, source=item.source)
    return item


@router.get("", response_model=list[InboxRead])
def list_inbox(db: Session = Depends(get_db)) -> Sequence[InboxItem]:
    return inbox_service.list_inbox_items(db)


@router.get("/{inbox_item_id}", response_model=InboxRead)
def get_inbox(inbox_item_id: int, db: Session = Depends(get_db)) -> InboxItem:
    return _get_inbox_or_404(db, inbox_item_id)


@router.post("/{inbox_item_id}/process", response_model=list[TaskRead])
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="extraction validation failed",
        ) from None
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
    _get_inbox_or_404(db, inbox_item_id)
    return inbox_service.list_candidates(db, inbox_item_id)


@router.post("/{inbox_item_id}/review", response_model=ReviewResult)
def review_inbox(
    inbox_item_id: int, data: ReviewRequest, db: Session = Depends(get_db)
) -> ReviewResult:
    item = _get_inbox_or_404(db, inbox_item_id)
    try:
        result = review_service.review_inbox(db, item, data.decisions)
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
