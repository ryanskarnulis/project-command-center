from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent


def record_event(
    db: Session,
    *,
    project_id: int | None,
    entity_type: str,
    entity_id: int,
    action: str,
    summary: str,
) -> ActivityEvent:
    """Append one activity event. The log is immutable, so this only ever inserts.

    Caller owns the transaction boundary. This helper only stages the row and
    flushes so the event id is available to callers before commit.
    """
    event = ActivityEvent(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        summary=summary,
    )
    db.add(event)
    db.flush()
    db.refresh(event)
    return event


def list_events(
    db: Session, project_id: int, *, limit: int = 50
) -> Sequence[ActivityEvent]:
    """Most-recent-first events for one project, capped at ``limit``.

    Writes its own ``select`` rather than using ``services.common.active`` because
    ``ActivityEvent`` is append-only and has no ``deleted_at`` to filter on.
    """
    return (
        db.execute(
            select(ActivityEvent)
            .where(ActivityEvent.project_id == project_id)
            .order_by(ActivityEvent.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
