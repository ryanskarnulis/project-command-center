from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import InboxItem, Project, Task, TaskStatus
from app.services.common import active


def _open_task_count(db: Session) -> int:
    """Total accepted (non-done, non-rejected, non-candidate) tasks."""
    result = db.execute(
        select(func.count()).select_from(
            active(Task).where(Task.status == TaskStatus.accepted).subquery()
        )
    ).scalar_one()
    return int(result)


def _per_project_open_counts(db: Session) -> Sequence[tuple[Project, int]]:
    """Active projects paired with their open-task count (may be 0)."""
    rows = db.execute(active(Project).order_by(Project.id)).scalars().all()
    counts: list[tuple[Project, int]] = []
    for project in rows:
        count = db.execute(
            select(func.count()).select_from(
                active(Task)
                .where(Task.project_id == project.id, Task.status == TaskStatus.accepted)
                .subquery()
            )
        ).scalar_one()
        counts.append((project, int(count)))
    return counts


def _resolved_project_id(db: Session, item: InboxItem) -> int | None:
    """The project this inbox item's tasks actually landed in.

    After review, accepted tasks carry the real project (which may differ from
    the pre-review suggestion when the user overrode it). Before review, we fall
    back to ``suggested_project_id`` so already-matched-but-not-yet-reviewed
    items still show the right destination.
    """
    accepted = (
        db.execute(
            active(Task).where(
                Task.inbox_item_id == item.id,
                Task.status == TaskStatus.accepted,
                Task.project_id.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    if accepted:
        # Modal project — first wins on a tie, matching review service behaviour.
        from collections import Counter
        counts: Counter[int] = Counter(
            t.project_id for t in accepted if t.project_id is not None
        )
        if counts:
            return counts.most_common(1)[0][0]
    return item.suggested_project_id


def _recent_inbox_items(
    db: Session, limit: int = 10
) -> Sequence[tuple[InboxItem, int | None]]:
    items = (
        db.execute(active(InboxItem).order_by(InboxItem.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [(item, _resolved_project_id(db, item)) for item in items]


def get_overview(
    db: Session,
) -> tuple[int, Sequence[tuple[Project, int]], Sequence[tuple[InboxItem, int | None]]]:
    """Return (total_open_tasks, per_project_counts, recent_inbox_items).

    No model calls. All three pieces come from existing tables.
    recent_inbox_items is a sequence of (InboxItem, resolved_project_id) pairs.
    """
    total = _open_task_count(db)
    per_project = _per_project_open_counts(db)
    recent = _recent_inbox_items(db)
    return total, per_project, recent
