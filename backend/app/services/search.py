"""Global search across projects and tasks.

Read-only, deterministic SQL ``LIKE`` over active (non-soft-deleted) rows. No model
call — this is plain navigation. Results are grouped by kind and each group is
independently capped so one noisy kind can't crowd out the other.
"""

from __future__ import annotations


from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.db.models import Project, Task, TaskWorkflowStatus
from app.schemas.search import SearchResultItem, SearchResults
from app.services.common import active

# Backslash-escape the LIKE metacharacters so a user typing "50%" or "a_b" searches
# for those literals instead of matching wildcards.
_LIKE_ESCAPE = "\\"

# Task matches are fetched whole (not pre-cut to ``per_kind``) so the effective-
# status tiebreak can influence *inclusion*, not just ordering within an
# already-fetched window — otherwise an older open match is dropped under a full
# page of newer done matches before its status is ever computed. This cap only
# guards a pathological wildcard; real matches on one user's SQLite file are tiny.
_TASK_SCAN_CAP = 500


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


def search(db: Session, query: str, *, per_kind: int = 8) -> SearchResults:
    """Find active projects/tasks matching ``query``.

    A blank query returns empty groups (the caller need not special-case it).
    """
    q = query.strip()
    if not q:
        return SearchResults(projects=[], tasks=[])

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

    # Inclusion must consider EFFECTIVE status, not just text tier + recency: a
    # checklist parent's stored ``workflow_status`` is never written back (see
    # ``tasks.compute_rollups``), so a SQL ``!= done`` predicate would misrank a
    # fully-done parent as not-done and a reopened-by-a-child leaf as done — and,
    # crucially, cutting to ``per_kind`` in SQL before status is known would drop
    # an older open match sitting behind a full page of newer done matches. So we
    # fetch the whole matching set (bounded by ``_TASK_SCAN_CAP``), resolve
    # effective status, sort by (tier, not-done-first, recency), and only then
    # slice to ``per_kind``. The tier is selected alongside the row so the sort
    # stays within tier. This mirrors ``tasks.list_tasks``' "read the matching set
    # whole, slice after filtering" tradeoff.
    task_text_score = _text_tier(Task.title, Task.description, q, prefix, contains)
    task_result = db.execute(
        active(Task)
        .add_columns(task_text_score.label("text_tier"))
        .where(
            or_(
                Task.title.ilike(contains, escape=_LIKE_ESCAPE),
                Task.description.ilike(contains, escape=_LIKE_ESCAPE),
            )
        )
        .order_by(task_text_score.asc(), Task.id.desc())
        .limit(_TASK_SCAN_CAP)
    ).all()
    task_rows = [row[0] for row in task_result]
    text_tiers = {row[0].id: row[1] for row in task_result}

    # Resolve blocked-aware, rolled-up status for the matched rows (≤
    # _TASK_SCAN_CAP) through the one shared source of truth every other read
    # surface uses. Local import: task_dependencies imports this package's siblings
    # and a module-level import would cycle (mirrors ``services/tasks.list_tasks``).
    from app.services import task_dependencies

    effective = task_dependencies.effective_statuses(db, [t.id for t in task_rows])

    def _effective(task: Task) -> TaskWorkflowStatus:
        return effective.get(task.id, task.workflow_status)

    # Apply the state tiebreak on effective status, still within text tier:
    # (tier, not-done-before-done, recency). Now that the full matching set is in
    # hand, this decides inclusion too — slice to ``per_kind`` only after sorting,
    # so a not-done match is never crowded out by newer done matches of equal tier.
    task_rows.sort(
        key=lambda t: (
            text_tiers[t.id],
            1 if _effective(t) == TaskWorkflowStatus.done else 0,
            -t.id,
        )
    )
    task_rows = task_rows[:per_kind]

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
                # Effective (rolled-up, blocked-aware) status so the command bar
                # filters /done candidates to what is really not-done — not the
                # stored column, which lies for checklist parents.
                workflow_status=_effective(t),
            )
            for t in task_rows
        ],
    )
