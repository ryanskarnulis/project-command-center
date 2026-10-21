"""search fts

Revision ID: e3f1a9c2b7d4
Revises: ada47a0f96cb
Create Date: 2026-10-20 22:30:00.000000

Word search (issue #444): SQLite FTS5 indexes so a query matches by its words
(any order, stop-words dropped by the service, porter-stemmed inflections)
instead of as one whole-query substring.

* ``tasks_fts(title, description)`` over ``tasks`` and
  ``projects_fts(name, description)`` over ``projects``: external-content FTS5
  tables (``content=`` the base table, ``content_rowid='id'``), so the text
  isn't stored twice; tokenizer ``porter unicode61 remove_diacritics 2``.
* Insert / delete / update-of-indexed-columns triggers on each base table keep
  the index in sync on every write path. Soft-deleted rows stay indexed;
  search filters them by joining back through ``active()``.
* Backfill: FTS5's ``'rebuild'`` command indexes every existing row from the
  content table.

Hand-written: autogenerate can't see virtual tables or triggers (env.py now
excludes the FTS tables from its comparison, so it won't propose dropping
them either). The DDL is a frozen copy of ``app.db.fts``, which ``create_all``
uses for tests and evals.

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e3f1a9c2b7d4'
down_revision: Union[str, None] = 'ada47a0f96cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TOKENIZE = "porter unicode61 remove_diacritics 2"

# (fts table, base table, indexed columns)
_INDEXES = (
    ("tasks_fts", "tasks", ("title", "description")),
    ("projects_fts", "projects", ("name", "description")),
)


def upgrade() -> None:
    for fts, base, columns in _INDEXES:
        cols = ", ".join(columns)
        new = ", ".join(f"new.{c}" for c in columns)
        old = ", ".join(f"old.{c}" for c in columns)
        delete_old = (
            f"INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', old.id, {old});"
        )
        insert_new = f"INSERT INTO {fts}(rowid, {cols}) VALUES (new.id, {new});"
        op.execute(
            f"CREATE VIRTUAL TABLE {fts} USING fts5({cols}, content='{base}', "
            f"content_rowid='id', tokenize='{_TOKENIZE}')"
        )
        op.execute(
            f"CREATE TRIGGER {fts}_ai AFTER INSERT ON {base} BEGIN {insert_new} END"
        )
        op.execute(
            f"CREATE TRIGGER {fts}_ad AFTER DELETE ON {base} BEGIN {delete_old} END"
        )
        op.execute(
            f"CREATE TRIGGER {fts}_au AFTER UPDATE OF {cols} ON {base} "
            f"BEGIN {delete_old} {insert_new} END"
        )
        # Backfill every existing row (soft-deleted included) from the base table.
        op.execute(f"INSERT INTO {fts}({fts}) VALUES ('rebuild')")


def downgrade() -> None:
    for fts, _base, _columns in _INDEXES:
        for suffix in ("ai", "ad", "au"):
            op.execute(f"DROP TRIGGER IF EXISTS {fts}_{suffix}")
        op.execute(f"DROP TABLE IF EXISTS {fts}")
