from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import Select, select

from app.db.models import SoftDeleteMixin

ModelT = TypeVar("ModelT", bound=SoftDeleteMixin)


def active(model: type[ModelT]) -> Select[tuple[ModelT]]:
    """Select non-soft-deleted rows of ``model`` (``deleted_at IS NULL``)."""
    return select(model).where(model.deleted_at.is_(None))


def deleted(model: type[ModelT]) -> Select[tuple[ModelT]]:
    """Select soft-deleted rows of ``model`` (``deleted_at IS NOT NULL``).

    The complement of ``active`` — used by the trash/restore views.
    """
    return select(model).where(model.deleted_at.is_not(None))


def soft_delete(obj: SoftDeleteMixin) -> None:
    """Mark a row deleted. Caller is responsible for committing."""
    obj.deleted_at = datetime.now(UTC)


def restore(obj: SoftDeleteMixin) -> None:
    """Clear a row's soft-delete mark. Caller is responsible for committing."""
    obj.deleted_at = None
