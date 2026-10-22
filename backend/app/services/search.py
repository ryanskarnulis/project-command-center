"""Global search across projects and tasks.

Read-only and deterministic over active (non-soft-deleted) rows. No model call —
this is plain navigation. Results are grouped by kind and each group is
independently capped so one noisy kind can't crowd out the other.

A row matches when the query's text appears in it (SQL ``LIKE``, so a fragment
such as "fire" still finds "firewall") **or** any of the query's words do: the
SQLite FTS5 indexes in ``app.db.fts`` (porter stemmer) match words in any order
and fold simple inflections, and stop-words are dropped from the query first
(#444). Relevance tiers, best first:

0. the primary text (task title / project name) equals the query;
1. it starts with the query;
2. it contains the query;
3. it contains every query word;
4. the description contains the query, or title + description hold every word;
5. some of the query words match (more matched words rank first).
"""

from __future__ import annotations

import re
from functools import reduce

from sqlalchemy import ColumnElement, Select, case, column, false, func, or_, select, table
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.db.models import Project, Task, TaskWorkflowStatus
from app.schemas.search import SearchResultItem, SearchResults
from app.services.common import active

# Backslash-escape the LIKE metacharacters so a user typing "50%" or "a_b" searches
# for those literals instead of matching wildcards.
_LIKE_ESCAPE = "\\"

# Words are runs of word characters; everything else (quotes, ``*``, ``-``,
# ``:``, parentheses, ``^``, emoji, NUL) is a separator. Each word then reaches
# FTS5 only as a double-quoted string, so a user's ``OR`` / ``NEAR`` /
# ``title:`` is a plain word to look for, never query syntax.
_WORD_RE = re.compile(r"\w+")

# Grammatical filler a spoken or paraphrased ask adds or drops ("sweep floor" vs
# "Sweep the floor"). Dropped from the query unless it's all the query has.
# Deliberately small and domain-free: words like "task" or "done" stay searchable.
_STOP_WORDS = frozenset(
    """
    a an and are as at be been but by do does for from had has have i in into is
    it its me my no nor not of off on or our so than that the their them then
    there these they this those to too up us was we were what when where which
    who will with you your
    """.split()
)

# Bounds the per-word subqueries a single search builds.
_MAX_QUERY_WORDS = 12

# The tier a row lands in when only some of the query's words match it.
_SOME_WORDS_TIER = 5

# Task matches are fetched whole (not pre-cut to ``per_kind``) so the effective-
# status tiebreak can influence *inclusion*, not just ordering within an
# already-fetched window — otherwise an older open match is dropped under a full
# page of newer done matches before its status is ever computed. This cap only
# guards a pathological wildcard; real matches on one user's SQLite file are tiny.
# It applies per stored-status bucket (see ``search``), so at most 2x this many
# rows are ever materialised.
_TASK_SCAN_CAP = 500


def _escape_like(term: str) -> str:
    return (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


def query_words(q: str) -> list[str]:
    """The words the query is matched by: lowercased, deduplicated, stop-words
    dropped (unless nothing else is left), capped at ``_MAX_QUERY_WORDS``."""
    words = list(dict.fromkeys(word.lower() for word in _WORD_RE.findall(q)))
    significant = [word for word in words if word not in _STOP_WORDS]
    return (significant or words)[:_MAX_QUERY_WORDS]


def fts_phrase(word: str) -> str:
    # ``word`` is ``\w+`` so it holds no double quote; doubling any anyway keeps
    # this safe if the word pattern ever widens.
    return '"' + word.replace('"', '""') + '"'


class _Matcher:
    """The match and rank expressions for one searchable kind and one query.

    ``primary``/``secondary`` are the base columns (title or name, then
    description); ``fts`` names that kind's FTS5 index from ``app.db.fts``,
    whose first column mirrors ``primary``.
    """

    def __init__(
        self,
        row_id: InstrumentedAttribute[int],
        primary: InstrumentedAttribute[str] | InstrumentedAttribute[str | None],
        secondary: InstrumentedAttribute[str | None],
        fts: str,
        q: str,
        words: list[str],
    ) -> None:
        self._row_id = row_id
        self._primary = primary
        self._secondary = secondary
        self._index = table(fts, column("rowid"), column(fts))
        self._match_column = self._index.c[fts]
        self._q = q
        self._words = words
        # Three escaped LIKE patterns share one escaped term: prefix and
        # substring use wildcards; the exact tier compares lowercased equality.
        escaped = _escape_like(q)
        self._prefix = f"{escaped}%"
        self._contains = f"%{escaped}%"
        self._primary_name = primary.key

    def _ids_matching(self, fts_query: str) -> Select[tuple[int]]:
        """Base-row ids whose index entry matches ``fts_query`` (a bound value)."""
        return select(self._index.c.rowid).where(self._match_column.op("MATCH")(fts_query))

    def _has(self, fts_query: str | None) -> ColumnElement[bool]:
        if fts_query is None:
            return false()
        return self._row_id.in_(self._ids_matching(fts_query))

    def _all_words(self, *, primary_only: bool) -> str | None:
        if not self._words:
            return None
        every = " AND ".join(fts_phrase(word) for word in self._words)
        return f"{{{self._primary_name}}} : ({every})" if primary_only else every

    def _any_word(self) -> str | None:
        if not self._words:
            return None
        return " OR ".join(fts_phrase(word) for word in self._words)

    def where(self) -> ColumnElement[bool]:
        """Text or any of the query's words, in either column."""
        return or_(
            self._primary.ilike(self._contains, escape=_LIKE_ESCAPE),
            self._secondary.ilike(self._contains, escape=_LIKE_ESCAPE),
            self._has(self._any_word()),
        )

    def tier(self) -> ColumnElement[int]:
        """The relevance tier in the module docstring (lower is better)."""
        return case(
            (func.lower(self._primary) == func.lower(self._q), 0),
            (self._primary.ilike(self._prefix, escape=_LIKE_ESCAPE), 1),
            (self._primary.ilike(self._contains, escape=_LIKE_ESCAPE), 2),
            (self._has(self._all_words(primary_only=True)), 3),
            (
                or_(
                    self._secondary.ilike(self._contains, escape=_LIKE_ESCAPE),
                    self._has(self._all_words(primary_only=False)),
                ),
                4,
            ),
            else_=_SOME_WORDS_TIER,
        )

    def word_hits(self) -> ColumnElement[int]:
        """How many distinct query words the row matches (orders the some-words
        tier; every other tier already holds the whole query)."""
        hits: list[ColumnElement[int]] = [
            case((self._has(fts_phrase(word)), 1), else_=0) for word in self._words
        ]
        if not hits:
            return case((false(), 1), else_=0)
        return reduce(lambda total, hit: total + hit, hits)


def search(db: Session, query: str, *, per_kind: int = 8) -> SearchResults:
    """Find active projects/tasks matching ``query``.

    A blank query returns empty groups (the caller need not special-case it).
    """
    q = query.strip()
    if not q:
        return SearchResults(projects=[], tasks=[])

    words = query_words(q)

    # Relevance ordering: best text tier first (within the some-words tier, more
    # matched words first), recency (id desc) as the tiebreak.
    projects = _Matcher(Project.id, Project.name, Project.description, "projects_fts", q, words)
    project_tier = projects.tier()
    project_rows = (
        db.execute(
            active(Project)
            .where(projects.where())
            .order_by(
                project_tier.asc(),
                case(
                    (project_tier == _SOME_WORDS_TIER, -projects.word_hits()), else_=0
                ).asc(),
                Project.id.desc(),
            )
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
    #
    # The cap itself has to respect that ordering, though: a single capped scan
    # ordered by (tier, id desc) hands Python only the newest _TASK_SCAN_CAP
    # matches, so a full cap's worth of newer *done* matches evicts an older open
    # one before its status is ever computed. We therefore scan two disjoint
    # buckets — stored-done and stored-not-done — each capped independently, and
    # merge them. Every stored-not-done match then reaches the relevance sort
    # regardless of how many done matches exist, which is the case the guarantee
    # is about. (Residual: a stored-done row that resolves to effectively
    # not-done can still be cut by _TASK_SCAN_CAP newer stored-done rows of the
    # same tier. Closing that would require resolving effective status for the
    # unbounded matching set, which is exactly what the cap exists to prevent.)
    tasks = _Matcher(Task.id, Task.title, Task.description, "tasks_fts", q, words)
    task_text_score = tasks.tier()
    matching = (
        active(Task)
        .add_columns(task_text_score.label("text_tier"), tasks.word_hits().label("word_hits"))
        .where(tasks.where())
        .order_by(task_text_score.asc(), Task.id.desc())
    )
    task_result = [
        row
        for stored_done in (False, True)
        for row in db.execute(
            matching.where(
                (Task.workflow_status == TaskWorkflowStatus.done)
                if stored_done
                else (Task.workflow_status != TaskWorkflowStatus.done)
            ).limit(_TASK_SCAN_CAP)
        ).all()
    ]
    task_rows = [row[0] for row in task_result]
    text_tiers = {row[0].id: row[1] for row in task_result}
    word_hits = {row[0].id: row[2] for row in task_result}

    # Resolve blocked-aware, rolled-up status for the matched rows (≤ 2 *
    # _TASK_SCAN_CAP) through the one shared source of truth every other read
    # surface uses. Local import: task_dependencies imports this package's siblings
    # and a module-level import would cycle (mirrors ``services/tasks.list_tasks``).
    from app.services import task_dependencies

    effective = task_dependencies.effective_statuses(db, [t.id for t in task_rows])

    def _effective(task: Task) -> TaskWorkflowStatus:
        return effective.get(task.id, task.workflow_status)

    # Apply the state tiebreak on effective status, still within text tier:
    # (tier, more-words-first in the some-words tier, not-done-before-done,
    # recency). Now that the full matching set is in hand, this decides inclusion
    # too — slice to ``per_kind`` only after sorting, so a not-done match is never
    # crowded out by newer done matches of equal relevance.
    task_rows.sort(
        key=lambda t: (
            text_tiers[t.id],
            -word_hits[t.id] if text_tiers[t.id] == _SOME_WORDS_TIER else 0,
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
