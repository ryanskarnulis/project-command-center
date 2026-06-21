from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.search import SearchResults
from app.services import search as search_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResults)
def search(
    q: str = Query(default="", max_length=200, description="Search text"),
    db: Session = Depends(get_db),
) -> SearchResults:
    """Global search across active projects, tasks, and inbox items.

    A blank/whitespace ``q`` returns empty groups so the client can debounce freely
    without special-casing the empty input.
    """
    results = search_service.search(db, q)
    logger.info(
        "search_performed",
        query_length=len(q.strip()),
        projects=len(results.projects),
        tasks=len(results.tasks),
        inbox_items=len(results.inbox_items),
    )
    return results
