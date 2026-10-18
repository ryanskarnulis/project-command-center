from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar

import structlog
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent

logger = structlog.get_logger(__name__)

# Who is driving the current write path. None means the user (the UI/API
# default); agent entrypoints bind their identifier (e.g. "agent:mcp") per
# tool call instead of threading an ``actor`` argument through every service
# signature — same pattern as the request-ID logging binding.
current_actor: ContextVar[str | None] = ContextVar("current_actor", default=None)


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
        actor=current_actor.get(),
    )
    db.add(event)
    db.flush()
    db.refresh(event)
    logger.info(
        "activity_event_recorded",
        event_id=event.id,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=event.actor,
    )
    return event


def record_task_events(
    db: Session,
    *,
    project_id: int,
    tasks: Sequence[tuple[int, str]],
    action: str,
    summary_suffix: str,
) -> None:
    """Append one task event per ``(task_id, title)`` in a single bulk insert.

    For cascades that touch every task in a project at once (a project restore
    can move tens of thousands of rows): ``record_event``'s flush-and-refresh
    per row would cost one round trip each. Each summary reads
    ``Task "<title>" <summary_suffix>``. Same append-only contract; ids are
    allocated in order, so these events land before anything the caller logs
    next.
    """
    if not tasks:
        return
    actor = current_actor.get()
    db.execute(
        insert(ActivityEvent),
        [
            {
                "project_id": project_id,
                "entity_type": "task",
                "entity_id": task_id,
                "action": action,
                "summary": f'Task "{title}" {summary_suffix}',
                "actor": actor,
            }
            for task_id, title in tasks
        ],
    )
    db.flush()
    logger.info(
        "activity_events_recorded",
        project_id=project_id,
        entity_type="task",
        action=action,
        count=len(tasks),
        actor=actor,
    )


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
