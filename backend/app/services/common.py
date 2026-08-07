from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db.models import SoftDeleteMixin

ModelT = TypeVar("ModelT", bound=SoftDeleteMixin)

# SQLite's bound-parameter ceiling is 32766 on modern builds but 999 on older
# ones, and several service paths feed a whole id list into an ``IN (...)``: the
# dependency closure walks a frontier per level, and ``trash.empty_trash`` hands
# over however many rows happen to sit in the trash. Chunking at 900 keeps the
# query legal everywhere for the cost of five lines.
IN_CHUNK = 900


def chunked(ids: Sequence[int], size: int = IN_CHUNK) -> Iterable[Sequence[int]]:
    """Split ``ids`` into slices small enough to bind as one ``IN (...)`` list.

    Lives here rather than in one service because the ceiling is a property of
    the database, not of any single caller: any query that expands a
    caller-sized or table-sized id list needs the same treatment. An empty input
    yields nothing, so callers don't need a separate empty-list guard.
    """
    for start in range(0, len(ids), size):
        yield ids[start : start + size]


def active(model: type[ModelT]) -> Select[tuple[ModelT]]:
    """Select non-soft-deleted rows of ``model`` (``deleted_at IS NULL``)."""
    return select(model).where(model.deleted_at.is_(None))


def deleted(model: type[ModelT]) -> Select[tuple[ModelT]]:
    """Select soft-deleted rows of ``model`` (``deleted_at IS NOT NULL``).

    The complement of ``active`` — used by the trash/restore views.
    """
    return select(model).where(model.deleted_at.is_not(None))


def count_deleted(db: Session, model: type[ModelT]) -> int:
    """Count soft-deleted rows of ``model``.

    Used by the trash count badge — a ``COUNT(*)`` so the number is exact even
    when there are more trashed rows than the ``/trash`` list page returns.
    """
    return (
        db.scalar(
            select(func.count()).select_from(model).where(model.deleted_at.is_not(None))
        )
        or 0
    )


def soft_delete(obj: SoftDeleteMixin) -> None:
    """Mark a row deleted. Caller is responsible for committing."""
    obj.deleted_at = datetime.now(UTC)


def restore(obj: SoftDeleteMixin) -> None:
    """Clear a row's soft-delete mark. Caller is responsible for committing."""
    obj.deleted_at = None


def hard_delete(db: Session, obj: SoftDeleteMixin) -> None:
    """Permanently remove a row from the database.

    Guard: refuses unless the row is already soft-deleted (in trash). Purge is the
    one true delete in the app (CLAUDE.md: "soft deletes only" — the user approved
    this for the trash purge); confining it to rows already in trash keeps an active
    row from ever being destroyed by a stray call. Callers must clean the row's FK
    edges first: FK enforcement is on (``PRAGMA foreign_keys = ON``, see
    ``db/session.py``), but SQLite FKs don't auto-cascade — a missed edge would
    *raise* on delete rather than clean up silently. The caller is responsible for
    committing.
    """
    if obj.deleted_at is None:
        raise ValueError("Only trashed items can be permanently deleted")
    db.delete(obj)
    db.flush()
