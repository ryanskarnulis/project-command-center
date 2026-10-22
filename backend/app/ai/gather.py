"""Gather likely matches before the loop's first model call (#415).

Most tool-using runs opened with a lookup — ``search``, ``list_tasks``,
``list_projects`` — before the model could act on what the user meant, and a
misheard or paraphrased name ("the dennis appointment") found nothing by
keyword at all. So before the first model call the loop searches the user's
own words and hands the top few tasks to the model beside the state block,
labelled as candidates: they may match, and nothing was done to them. They are
never a tool result.

Ranking is hybrid, borrowed from chess (``gather.py``, ``knowledge.Index.hybrid``):

- **Keywords:** how many of the query's words an item holds
  (``services.search.query_words``; tasks through the porter-stemmed
  ``tasks_fts``, with FTS5 BM25 — title over description — breaking ties;
  commitment titles by word or shared prefix).
- **Meaning:** EmbeddingGemma 2 vectors from the shared embeddings service
  (``app.ai.embeddings``); the cosine is a dot product, pure Python.
- **The bar decides whether a task counts; RRF only orders what passed.** Raw
  cosines are compressed (everything scores 0.55–0.75), so the bar is a task's
  *lift* over the median cosine of the corpus for this query: one that stands
  well clear alone (:data:`LIFT_ALONE`), or a little clear with a keyword hit
  behind it (:data:`LIFT_SUPPORT`). Calibrated on the dev corpus and the eval
  worlds (2026-10-22): a true hit lifts +0.2–0.3, chit-chat and "what's due
  today?" stay under +0.11.

Tasks and live Focus commitments (a meeting, a repeating standup) are
searched: a commitment is named like a task, and with only tasks shown the
model skipped the look-alike recurring task "Prepare standup notes" for "skip
today's team standup" (``focus_skip_series``, 0/10). Projects are not: the
open ones are already in the state block, and bare project names ("Home",
"Work") sit close to every query.

A stopped or slow service costs recall, never the run: the query embeds with a
0.3 s timeout, and anything short of usable vectors falls back to keywords
alone (``source="keyword"``). Building the matches never raises.

Vectors stay in this process — a dict, persisted as one JSON file when a path
is configured — keyed on a hash of each task's embedded text, under the model
the server names and the service build from config (the service doesn't
expose its build). An edited task's text hashes differently, so it is simply
missing; missing ones are embedded on a background thread, never on a write
path, and rank by keyword until they are in. That catches writes from every
process (the MCP server included) with no hook.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import structlog
from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from app.ai.embeddings import (
    GATHER_TIMEOUT,
    Embedder,
    EmbeddingsUnavailable,
    document_text,
    query_text,
)
from app.api.task_reads import reads_with_blocked
from app.config import get_settings
from app.db.models import FocusPlanEntry, Task
from app.services import task_dependencies as deps_service
from app.services.common import active
from app.services.search import fts_phrase, query_words
from app.tools import runtime, views

logger = structlog.get_logger(__name__)

Source = Literal["hybrid", "keyword", "none"]
Kind = Literal["task", "commitment"]
# One searchable thing: a task or a Focus commitment, by its id.
Item = tuple[Kind, int]

# Matches handed to the model.
MAX_MATCHES = 5
# Reciprocal rank fusion's constant (the usual 60, as chess).
RRF_K = 60
# A task's cosine minus the corpus median for the query. Clearing LIFT_ALONE
# counts on meaning alone; clearing LIFT_SUPPORT counts with a keyword hit.
LIFT_ALONE = 0.15
LIFT_SUPPORT = 0.05
# Below this many embedded tasks a median says nothing: keywords alone.
MIN_VECTOR_CORPUS = 5
# Keyword-only mode keeps tasks scoring at least this share of the best.
KEYWORD_RELATIVE_CUTOFF = 0.5
# BM25 column weights: title, description.
BM25_WEIGHTS = (3.0, 1.0)
# A description is embedded up to this many characters.
MAX_DESCRIPTION_CHARS = 300
# After a failed warm, wait this long before the next try.
RETRY_WARM_S = 60.0

# Rows name their id ``task_id`` (and a waiting task's edges their
# ``dependency_id``): with bare ``id``s an early cut's model used a task id as
# a dependency id. A label naming what is *not* searched (Focus entries,
# dependencies) was tried and dropped: it cost ``dependency_add`` 4/10 runs
# that claimed a write and made none (2026-10-22).
LABEL = (
    "[Possible matches for the user's words — tasks and Focus commitments a search "
    "found before this run. They may match; nothing was done to them]"
)
_FOOTER = "[End of possible matches]"


def task_document(title: str, description: str | None) -> str:
    """A task as embedded: its title, then the start of its description."""
    body = " ".join((description or "").split())[:MAX_DESCRIPTION_CHARS]
    return document_text(f"{title}. {body}" if body else title)


def document_key(document: str) -> str:
    return hashlib.sha256(document.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Match:
    item: Item
    score: float
    lift: float | None = None
    keyword: float | None = None


@dataclass
class Gathered:
    """What the gather found, how, and how long it took."""

    matches: list[Match] = field(default_factory=list)
    source: Source = "none"
    model: str | None = None
    ms: float = 0.0

    def as_trace(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model": self.model,
            "ms": round(self.ms, 1),
            "matches": [
                {
                    "kind": m.item[0],
                    "id": m.item[1],
                    "score": round(m.score, 4),
                    "lift": None if m.lift is None else round(m.lift, 4),
                    "keyword": None if m.keyword is None else round(m.keyword, 3),
                }
                for m in self.matches
            ],
        }


@dataclass
class VectorCache:
    """Vectors by document key, all from one model and service build."""

    model: str | None = None
    build: str = ""
    vectors: dict[str, list[float]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None, build: str) -> VectorCache:
        """The saved cache, or an empty one when there is none, it doesn't
        parse, or it is from another build."""
        if path is None:
            return cls(build=build)
        try:
            body = json.loads(path.read_text())
            if body.get("build") != build:
                return cls(build=build)
            vectors = {str(k): [float(x) for x in v] for k, v in body["vectors"].items()}
            return cls(model=str(body["model"]), build=build, vectors=vectors)
        except FileNotFoundError:
            return cls(build=build)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("gather_cache_unreadable", path=str(path), error=str(exc))
            return cls(build=build)

    def save(self, path: Path) -> None:
        """Atomic: a crash mid-write leaves the old file, never half a new one."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"model": self.model, "build": self.build, "vectors": self.vectors})
        )
        os.replace(tmp, path)


@dataclass(frozen=True)
class _Doc:
    item: Item
    key: str
    text: str


def _live_commitments(db: Session, today: date) -> list[FocusPlanEntry]:
    """Commitments that still happen: a series not stopped by today, or a
    one-off from today on. Pins, skips and overrides are not commitments."""
    return list(
        db.execute(
            active(FocusPlanEntry).where(
                FocusPlanEntry.task_id.is_(None),
                FocusPlanEntry.series_id.is_(None),
                FocusPlanEntry.title.is_not(None),
                or_(
                    and_(
                        FocusPlanEntry.repeat_interval.is_not(None),
                        or_(FocusPlanEntry.stop_date.is_(None), FocusPlanEntry.stop_date > today),
                    ),
                    and_(
                        FocusPlanEntry.repeat_interval.is_(None),
                        FocusPlanEntry.plan_date >= today,
                    ),
                ),
            )
        ).scalars()
    )


def _corpus(db: Session, today: date) -> list[_Doc]:
    docs = []
    tasks = db.execute(
        select(Task.id, Task.title, Task.description).where(Task.deleted_at.is_(None))
    ).tuples()
    for task_id, title, description in tasks:
        document = task_document(title, description)
        docs.append(_Doc(("task", task_id), document_key(document), document))
    for entry in _live_commitments(db, today):
        document = document_text(entry.title or "")
        docs.append(_Doc(("commitment", entry.id), document_key(document), document))
    return docs


def _word_matches(word: str, title_words: set[str]) -> bool:
    """A commitment title holds ``word``: the same word, or one a prefix of
    the other ("standup"/"standups"), as the tasks' stemmer roughly would."""
    return any(
        word == t or (min(len(word), len(t)) >= 4 and (t.startswith(word) or word.startswith(t)))
        for t in title_words
    )


def _keyword_scores(db: Session, words: Sequence[str], today: date) -> dict[Item, float]:
    """Per item holding any of ``words``: how many it holds, plus a fraction
    under one from FTS5 BM25 that orders tasks holding as many."""
    if not words:
        return {}
    scores: dict[Item, float] = {}
    for word in words:
        hit = db.execute(
            text(
                "SELECT tasks_fts.rowid FROM tasks_fts JOIN tasks ON tasks.id = tasks_fts.rowid "
                "WHERE tasks_fts MATCH :q AND tasks.deleted_at IS NULL"
            ),
            {"q": fts_phrase(word)},
        ).scalars()
        for task_id in hit:
            scores[("task", int(task_id))] = scores.get(("task", int(task_id)), 0.0) + 1
    if scores:
        title_w, description_w = BM25_WEIGHTS
        bm25 = db.execute(
            text(
                f"SELECT tasks_fts.rowid, bm25(tasks_fts, {title_w}, {description_w}) "
                "FROM tasks_fts WHERE tasks_fts MATCH :q"
            ),
            {"q": " OR ".join(fts_phrase(word) for word in words)},
        ).tuples()
        for task_id, score in bm25:
            item: Item = ("task", int(task_id))
            if item in scores:
                # FTS5's bm25() is negative, more negative for a better match.
                good = max(0.0, -float(score))
                scores[item] += good / (1 + good)
    for entry in _live_commitments(db, today):
        title_words = set(query_words(entry.title or ""))
        hits = sum(1 for word in words if _word_matches(word, title_words))
        if hits:
            scores[("commitment", entry.id)] = float(hits)
    return scores


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _order(m: Match) -> tuple[float, int, int]:
    # Best first; ties to the newer item, tasks before commitments.
    return (-m.score, -m.item[1], m.item[0] != "task")


def rank_hybrid(cosines: dict[Item, float], keyword: dict[Item, float]) -> list[Match]:
    """The items clearing the bar, best first by RRF over both rankings.

    An item with no vector yet (``keyword`` only) counts on its keyword hit.
    """
    median = statistics.median(cosines.values())
    lifts = {item: cosine - median for item, cosine in cosines.items()}

    def clears(item: Item) -> bool:
        lift = lifts.get(item)
        if lift is None:
            return item in keyword
        return lift >= LIFT_ALONE or (lift >= LIFT_SUPPORT and item in keyword)

    fused: dict[Item, float] = {}
    by_meaning = sorted(cosines, key=lambda item: -cosines[item])
    by_keyword = sorted(keyword, key=lambda item: -keyword[item])
    for ranking in (by_meaning, by_keyword):
        for rank, item in enumerate(ranking):
            fused[item] = fused.get(item, 0.0) + 1 / (RRF_K + rank + 1)
    passed = [
        Match(item, score, lifts.get(item), keyword.get(item))
        for item, score in fused.items()
        if clears(item)
    ]
    passed.sort(key=_order)
    return passed[:MAX_MATCHES]


def rank_keyword(keyword: dict[Item, float]) -> list[Match]:
    """Keywords alone: the best few, cut at a share of the best score."""
    if not keyword:
        return []
    best = max(keyword.values())
    kept = [
        Match(item, score, keyword=score)
        for item, score in keyword.items()
        if score >= best * KEYWORD_RELATIVE_CUTOFF
    ]
    kept.sort(key=_order)
    return kept[:MAX_MATCHES]


class Searcher:
    """The gather and its vector cache, shared by every run in the process."""

    def __init__(
        self,
        embedder: Embedder | None,
        *,
        build: str = "",
        cache_path: Path | None = None,
    ) -> None:
        self._embedder = embedder
        self._build = build
        self._cache_path = cache_path
        self._cache = VectorCache.load(cache_path, build) if embedder else VectorCache()
        self._lock = threading.Lock()
        self._warming = threading.Lock()
        self._last_failure = 0.0
        # Off where a second connection isn't safe (the evals' one shared
        # in-memory connection); the caller then warms before each run.
        self.background_warm = True

    @property
    def hybrid(self) -> bool:
        return self._embedder is not None

    def gather(self, utterance: str) -> Gathered:
        """The likely matches for ``utterance``. Never raises."""
        # perf_counter, not monotonic: tests fake the loop's deadline clock.
        started = time.perf_counter()
        try:
            gathered = self._gather(utterance)
        except Exception as exc:
            logger.warning(
                "gather_failed", error=str(exc), error_type=type(exc).__name__
            )
            gathered = Gathered()
        gathered.ms = (time.perf_counter() - started) * 1000
        return gathered

    def _gather(self, utterance: str) -> Gathered:
        if not utterance.strip():
            return Gathered()
        today = date.today()
        with runtime.session_factory() as db:
            docs = _corpus(db, today) if self._embedder is not None else []
            keyword = _keyword_scores(db, query_words(utterance), today)
        cosines, model = self._cosines(utterance, docs)
        if cosines is None:
            return Gathered(rank_keyword(keyword), "keyword")
        # Keyword hits on items with no vector yet still count (``rank_hybrid``).
        return Gathered(rank_hybrid(cosines, keyword), "hybrid", model)

    def _cosines(
        self, utterance: str, docs: list[_Doc]
    ) -> tuple[dict[Item, float] | None, str | None]:
        """Cosines per embedded item, or ``None`` when meaning can't be used."""
        if self._embedder is None:
            return None, None
        with self._lock:
            model = self._cache.model
            vectors = {d.item: self._cache.vectors.get(d.key) for d in docs}
        if any(vector is None for vector in vectors.values()):
            self.warm_in_background()
        embedded = {item: v for item, v in vectors.items() if v is not None}
        if len(embedded) < MIN_VECTOR_CORPUS:
            return None, None
        try:
            query = self._embedder.embed([query_text(utterance)], timeout=GATHER_TIMEOUT)
        except EmbeddingsUnavailable as exc:
            logger.info("gather_fell_back", reason=str(exc))
            self.warm_in_background()
            return None, None
        if query.model != model:
            # The service now runs another model: every vector is stale.
            logger.info("gather_fell_back", reason=f"model {model} → {query.model}")
            with self._lock:
                self._cache = VectorCache(build=self._build)
            self.warm_in_background()
            return None, None
        q = query.vectors[0]
        try:
            return {item: _dot(q, v) for item, v in embedded.items()}, model
        except ValueError:
            # Vectors of another length: the cache is from something else.
            with self._lock:
                self._cache = VectorCache(build=self._build)
            self.warm_in_background()
            return None, None

    def warm(self) -> bool:
        """Embed every item with no vector, drop the vectors of items that are
        gone, save. ``True`` when the cache now covers the corpus."""
        if self._embedder is None:
            return False
        with self._warming:
            with runtime.session_factory() as db:
                docs = _corpus(db, date.today())
            with self._lock:
                model, vectors = self._cache.model, dict(self._cache.vectors)
            missing = {d.key: d.text for d in docs if d.key not in vectors}
            if missing:
                try:
                    embedded = self._embedder.embed_documents(list(missing.values()))
                except EmbeddingsUnavailable as exc:
                    self._last_failure = time.monotonic()
                    logger.info("gather_warm_failed", error=str(exc))
                    return False
                if embedded.model != model:
                    # Another model: none of the old vectors compare with these.
                    vectors = {}
                model = embedded.model
                vectors.update(zip(missing, embedded.vectors, strict=True))
            live = {d.key for d in docs}
            cache = VectorCache(
                model, self._build, {key: v for key, v in vectors.items() if key in live}
            )
            with self._lock:
                self._cache = cache
            if missing and self._cache_path is not None:
                try:
                    cache.save(self._cache_path)
                except OSError as exc:
                    logger.warning("gather_cache_save_failed", error=str(exc))
            covered = len(cache.vectors) == len(live)
            logger.info("gather_warmed", embedded=len(missing), total=len(cache.vectors))
            return covered

    def warm_in_background(self) -> None:
        """:meth:`warm` on a daemon thread, unless one is running or the last
        failed under :data:`RETRY_WARM_S` ago."""
        if not self.background_warm or self._embedder is None or self._warming.locked():
            return
        if self._last_failure and time.monotonic() - self._last_failure < RETRY_WARM_S:
            return
        threading.Thread(target=self._warm_quietly, name="gather-warm", daemon=True).start()

    def _warm_quietly(self) -> None:
        try:
            self.warm()
        except Exception as exc:
            self._last_failure = time.monotonic()
            logger.warning(
                "gather_warm_crashed", error=str(exc), error_type=type(exc).__name__
            )


def rows(gathered: Gathered) -> list[dict[str, Any]]:
    """The matches as the model reads them, best first: a task as the #414 task
    row, a commitment by its Focus ``entry_id``. Empty when there are none, or
    on any failure (never raises)."""
    if not gathered.matches:
        return []
    try:
        task_ids = [i for kind, i in (m.item for m in gathered.matches) if kind == "task"]
        entry_ids = [i for kind, i in (m.item for m in gathered.matches) if kind == "commitment"]
        with runtime.session_factory() as db:
            tasks = db.execute(active(Task).where(Task.id.in_(task_ids))).scalars().all()
            reads = {read.id: read for read in reads_with_blocked(db, tasks)}
            names = views.project_names(db, (read.project_id for read in reads.values()))
            entries = {
                entry.id: entry
                for entry in db.execute(
                    active(FocusPlanEntry).where(FocusPlanEntry.id.in_(entry_ids))
                ).scalars()
            }
            out: list[dict[str, Any]] = []
            for kind, item_id in (m.item for m in gathered.matches):
                if kind == "task" and item_id in reads:
                    out.append(_as_match(db, views.task_row(reads[item_id], names)))
                elif kind == "commitment" and item_id in entries:
                    out.append(_commitment_row(entries[item_id]))
            return out
    except Exception as exc:
        logger.warning("gather_rows_failed", error=str(exc), error_type=type(exc).__name__)
        return []


def _commitment_row(entry: FocusPlanEntry) -> dict[str, Any]:
    """A Focus commitment: its ``entry_id`` (a series' id for
    ``skip_focus_occurrence``), title, time, and how it repeats or its day."""
    minute = entry.start_minute or 0
    row: dict[str, Any] = {
        "entry_id": entry.id,
        "title": entry.title,
        "focus_commitment": True,
        "at": f"{minute // 60:02d}:{minute % 60:02d}",
    }
    if entry.repeat_interval is not None:
        row["repeats"] = entry.repeat_interval
    else:
        row["date"] = entry.plan_date.isoformat()
    return row


def _as_match(db: Session, row: dict[str, Any]) -> dict[str, Any]:
    """The task row with its ``id`` named ``task_id`` and, when it waits on
    others, each edge's ``dependency_id``: with both task ids in hand, an
    early cut's model guessed the edge id to remove instead of listing it."""
    match = {"task_id": row.pop("id"), **row}
    edges = deps_service.list_dependencies(db, match["task_id"])
    if edges:
        titles = dict(
            db.execute(
                select(Task.id, Task.title).where(
                    Task.id.in_([e.depends_on_task_id for e in edges])
                )
            ).tuples()
        )
        match["waits_on"] = [
            {
                "dependency_id": e.id,
                "task_id": e.depends_on_task_id,
                "title": titles.get(e.depends_on_task_id),
            }
            for e in edges
        ]
    return match


def section(matches: Sequence[dict[str, Any]]) -> str | None:
    """The rows under their label, one per line; ``None`` for no rows."""
    if not matches:
        return None
    lines = [LABEL, *(f"- {json.dumps(row, ensure_ascii=False)}" for row in matches), _FOOTER]
    return "\n".join(lines)


def searcher_from_settings() -> Searcher:
    settings = get_settings()
    embedder = (
        Embedder(settings.embeddings_base_url) if settings.embeddings_base_url else None
    )
    return Searcher(
        embedder,
        build=settings.embeddings_build,
        cache_path=settings.embeddings_cache_path,
    )


@lru_cache
def default_searcher() -> Searcher:
    """The process's searcher: one vector cache for every run."""
    return searcher_from_settings()
