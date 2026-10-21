"""SQLite FTS5 word-search indexes over task and project text (#444).

Two external-content FTS5 tables mirror the searchable columns of their base
table — ``tasks_fts(title, description)`` over ``tasks`` and
``projects_fts(name, description)`` over ``projects`` — keyed by the base row's
id (``rowid``). The ``porter`` tokenizer folds simple inflections
(paint/painting, buy/buying) and ``unicode61 remove_diacritics 2`` folds case
and accents.

Triggers keep each index in sync with its table, so no write path (service,
migration backfill, or a hand-run SQL fix) can drift it. The index holds every
row, soft-deleted ones included: ``services/search`` joins back to the base
table through the ``active`` helper, so trash filtering stays in one place and
a restore needs no reindex.

The DDL lives here so the two ways a schema gets built agree: the Alembic
migration (``20261020_e3f1a9c2b7d4``, which keeps its own frozen copy) and
``Base.metadata.create_all`` (tests and evals), which runs ``CREATE_STATEMENTS``
through an ``after_create`` hook registered in ``app.db.models``.

Caveat for future migrations: a batch-mode rebuild of ``tasks`` or
``projects`` (any CHECK/constraint change on SQLite) drops and recreates the
table, and SQLite drops a table's triggers with it. Such a migration must run
``CREATE_STATEMENTS`` again afterwards (the ``IF NOT EXISTS`` guards make that
safe); ``tests/test_migrations.py`` asserts the triggers exist at head.
"""

from __future__ import annotations

# (fts table, base table, indexed columns). The first column is the "primary"
# text search ranks highest (task title / project name).
FTS_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("tasks_fts", "tasks", ("title", "description")),
    ("projects_fts", "projects", ("name", "description")),
)

TOKENIZE = "porter unicode61 remove_diacritics 2"


def _statements_for(fts: str, base: str, columns: tuple[str, ...]) -> list[str]:
    cols = ", ".join(columns)
    new = ", ".join(f"new.{c}" for c in columns)
    old = ", ".join(f"old.{c}" for c in columns)
    delete_old = (
        f"INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', old.id, {old});"
    )
    insert_new = f"INSERT INTO {fts}(rowid, {cols}) VALUES (new.id, {new});"
    return [
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5("
        f"{cols}, content='{base}', content_rowid='id', tokenize='{TOKENIZE}')",
        f"CREATE TRIGGER IF NOT EXISTS {fts}_ai AFTER INSERT ON {base} BEGIN "
        f"{insert_new} END",
        f"CREATE TRIGGER IF NOT EXISTS {fts}_ad AFTER DELETE ON {base} BEGIN "
        f"{delete_old} END",
        # Only a change to an indexed column touches the index: status, dates
        # and soft-delete stamps write the row often and must not churn it.
        f"CREATE TRIGGER IF NOT EXISTS {fts}_au AFTER UPDATE OF {cols} ON {base} "
        f"BEGIN {delete_old} {insert_new} END",
    ]


CREATE_STATEMENTS: tuple[str, ...] = tuple(
    stmt for fts, base, columns in FTS_INDEXES for stmt in _statements_for(fts, base, columns)
)

DROP_STATEMENTS: tuple[str, ...] = tuple(
    stmt
    for fts, _base, _columns in FTS_INDEXES
    for stmt in (
        f"DROP TRIGGER IF EXISTS {fts}_ai",
        f"DROP TRIGGER IF EXISTS {fts}_ad",
        f"DROP TRIGGER IF EXISTS {fts}_au",
        f"DROP TABLE IF EXISTS {fts}",
    )
)

# FTS5 keeps its data in shadow tables named ``<fts>_<suffix>``.
_SHADOW_SUFFIXES = ("data", "idx", "content", "docsize", "config")
FTS_TABLE_NAMES: frozenset[str] = frozenset(
    name
    for fts, _base, _columns in FTS_INDEXES
    for name in (fts, *(f"{fts}_{suffix}" for suffix in _SHADOW_SUFFIXES))
)
