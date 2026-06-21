"""Global search across projects, tasks, and inbox items.

Read-only, deterministic SQL ``LIKE`` over active (non-soft-deleted) rows. No model
call and no AI surface — this is plain navigation, not extraction. Results are grouped
by kind and each group is independently capped so one noisy kind can't crowd out the
others.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.db.models import InboxItem, Project, Task, TaskReviewStatus, TaskWorkflowStatus
from app.schemas.search import SearchResultItem, SearchResults
from app.services.common import active

# Backslash-escape the LIKE metacharacters so a user typing "50%" or "a_b" searches
# for those literals instead of matching wildcards.
_LIKE_ESCAPE = "\\"


def _escape_like(term: str) -> str:
    return (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


def _text_tier(
    primary: InstrumentedAttribute[str | None],
    secondary: InstrumentedAttribute[str | None],
    q: str,
    prefix: str,
    contains: str,
) -> ColumnElement[int]:
    """0=exact, 1=prefix, 2=substring on ``primary``; 3=``secondary`` only.

    Lower is better. Mirrors the existing ``ilike(..., escape=_LIKE_ESCAPE)`` matching
    so ordering stays consistent with the ``WHERE`` clause.
    """
    return case(
        (func.lower(primary) == func.lower(q), 0),
        (primary.ilike(prefix, escape=_LIKE_ESCAPE), 1),
        (primary.ilike(contains, escape=_LIKE_ESCAPE), 2),
        else_=3,
    )


def _inbox_title(item: InboxItem) -> str:
    """Prefer the AI summary as the display line; fall back to trimmed raw text."""
    if item.summary:
        return item.summary
    text = item.raw_text.strip()
    return text[:80] + "…" if len(text) > 80 else text


def search(db: Session, query: str, *, per_kind: int = 8) -> SearchResults:
    """Find active projects/tasks/inbox items matching ``query``.

    A blank query returns empty groups (the caller need not special-case it).
    """
    q = query.strip()
    if not q:
        return SearchResults(projects=[], tasks=[], inbox_items=[])

    # Three escaped LIKE patterns share one escaped term: prefix and substring use
    # wildcards; the exact tier compares lowercased equality (no LIKE needed).
    escaped = _escape_like(q)
    prefix = f"{escaped}%"
    contains = f"%{escaped}%"

    # Relevance ordering: best text tier first, recency (id desc) as the tiebreak.
    project_score = _text_tier(Project.name, Project.description, q, prefix, contains)
    project_rows = (
        db.execute(
            active(Project)
            .where(
                or_(
                    Project.name.ilike(contains, escape=_LIKE_ESCAPE),
                    Project.description.ilike(contains, escape=_LIKE_ESCAPE),
                )
            )
            .order_by(project_score.asc(), Project.id.desc())
            .limit(per_kind)
        )
        .scalars()
        .all()
    )

    # Text tier wins first; state only breaks ties within the same text tier.
    task_text_score = _text_tier(Task.title, Task.description, q, prefix, contains)
    task_state_score = case(
        (
            (Task.review_status == TaskReviewStatus.accepted)
            & (Task.workflow_status != TaskWorkflowStatus.done),
            0,
        ),
        else_=1,
    )
    task_rows = (
        db.execute(
            active(Task)
            .where(
                or_(
                    Task.title.ilike(contains, escape=_LIKE_ESCAPE),
                    Task.description.ilike(contains, escape=_LIKE_ESCAPE),
                )
            )
            .order_by(task_text_score.asc(), task_state_score.asc(), Task.id.desc())
            .limit(per_kind)
        )
        .scalars()
        .all()
    )

    # Resolve owning-project names for the task subtitles in one query (no N+1).
    project_ids = {t.project_id for t in task_rows if t.project_id is not None}
    names: dict[int, str] = {}
    if project_ids:
        names = dict(
            db.execute(
                select(Project.id, Project.name).where(Project.id.in_(project_ids))
            )
            .tuples()
            .all()
        )

    # Inbox: the summary is the human-facing line, so a summary hit beats a raw-text-
    # only hit.
    inbox_score = case(
        (InboxItem.summary.ilike(contains, escape=_LIKE_ESCAPE), 0),
        else_=1,
    )
    inbox_rows = (
        db.execute(
            active(InboxItem)
            .where(
                or_(
                    InboxItem.raw_text.ilike(contains, escape=_LIKE_ESCAPE),
                    InboxItem.summary.ilike(contains, escape=_LIKE_ESCAPE),
                )
            )
            .order_by(inbox_score.asc(), InboxItem.id.desc())
            .limit(per_kind)
        )
        .scalars()
        .all()
    )

    return SearchResults(
        projects=[
            SearchResultItem(
                kind="project", id=p.id, title=p.name, subtitle=p.description
            )
            for p in project_rows
        ],
        tasks=[
            SearchResultItem(
                kind="task",
                id=t.id,
                title=t.title,
                subtitle=names.get(t.project_id) if t.project_id is not None else None,
                project_id=t.project_id,
                # Serialized off existing columns (no extra query) so the command
                # bar can filter /done candidates to accepted, not-done tasks.
                review_status=t.review_status,
                workflow_status=t.workflow_status,
            )
            for t in task_rows
        ],
        inbox_items=[
            SearchResultItem(kind="inbox", id=item.id, title=_inbox_title(item))
            for item in inbox_rows
        ],
    )
