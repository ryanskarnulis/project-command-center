"""Classifying database integrity errors into domain meanings.

A service that races another writer has to tell "this exact uniqueness rule was
violated" apart from "something else in the schema broke". SQLite does *not*
name the index it rejected — it reports the constrained columns, e.g.::

    UNIQUE constraint failed: tasks.recurrence_id, tasks.due_date

so matching on an index name never fires. This module owns that one job: turn a
driver-level ``IntegrityError`` into an answer about a specific
``(table, columns)`` uniqueness key, and say *no* for anything it cannot prove.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError

__all__ = ["violates_unique_columns"]

_UNIQUE_MESSAGE = re.compile(r"UNIQUE constraint failed:\s*(?P<columns>[^\n]+)")

# The uniqueness-flavoured constraint codes. A PRIMARY KEY collision is reported
# under its own name but with the same message shape.
_UNIQUE_ERRORNAMES = frozenset(
    {"SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY"}
)


def _unique_violation_columns(exc: IntegrityError) -> frozenset[str] | None:
    """The ``table.column`` set SQLite says was violated, or ``None``.

    ``None`` means "not a SQLite uniqueness failure I can read" — a foreign-key
    or NOT NULL breach, another dialect, or a message shape this doesn't parse.
    Callers must treat that as "re-raise", never as "no match, carry on".
    """
    orig = exc.orig
    if not isinstance(orig, sqlite3.IntegrityError):
        return None
    errorname = getattr(orig, "sqlite_errorname", None)
    if errorname is not None and errorname not in _UNIQUE_ERRORNAMES:
        return None
    match = _UNIQUE_MESSAGE.search(str(orig))
    if match is None:
        return None
    return frozenset(part.strip() for part in match.group("columns").split(","))


def violates_unique_columns(
    exc: IntegrityError, table: str, columns: Sequence[str]
) -> bool:
    """Is ``exc`` the uniqueness failure of exactly ``table``'s ``columns``?

    The column tuple is the only thing SQLite tells us, so it — not the index
    name — is the identity of the rule. Partial and full unique indexes over the
    same columns are indistinguishable here; each call site pairs this with its
    own operation context (it just attempted one specific insert) to make the
    translation sound. Anything else, including a uniqueness failure on *other*
    columns, returns ``False`` so the caller re-raises.
    """
    violated = _unique_violation_columns(exc)
    if violated is None:
        return False
    return violated == frozenset(f"{table}.{column}" for column in columns)
