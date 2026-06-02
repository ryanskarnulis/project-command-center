from __future__ import annotations

import hashlib
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import InboxItem, InboxSource, Task
from app.services.common import active, deleted, restore, soft_delete


class RestoreConflictError(Exception):
    """Restoring a dismissed inbox item whose text was re-captured since.

    The active partial unique index on ``input_hash`` would be violated, so the
    restore is refused (the caller surfaces a 409). The re-captured active item
    already represents this note.
    """


def hash_text(text: str) -> str:
    """Stable SHA-256 hex digest of the raw inbox text (idempotency key)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_inbox_item(
    db: Session,
    *,
    raw_text: str,
    source: InboxSource = InboxSource.web,
) -> InboxItem:
    """Create an inbox item, idempotently.

    If an active inbox item already exists with the same input hash, return it
    unchanged — re-submitting the same text must not create a duplicate row (and
    therefore must not lead to duplicate candidate extraction downstream).
    """
    input_hash = hash_text(raw_text)
    existing = db.execute(
        active(InboxItem).where(InboxItem.input_hash == input_hash)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    item = InboxItem(raw_text=raw_text, input_hash=input_hash, source=source)
    db.add(item)
    try:
        db.flush()
        db.refresh(item)
        return item
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            active(InboxItem).where(InboxItem.input_hash == input_hash)
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing


def get_inbox_item(db: Session, inbox_item_id: int) -> InboxItem | None:
    return db.execute(
        active(InboxItem).where(InboxItem.id == inbox_item_id)
    ).scalar_one_or_none()


def list_inbox_items(db: Session) -> Sequence[InboxItem]:
    return db.execute(active(InboxItem).order_by(InboxItem.id)).scalars().all()


def list_pending_review_items(
    db: Session, *, limit: int = 50
) -> Sequence[InboxItem]:
    """Processed inbox items still awaiting review, newest first."""
    return (
        db.execute(
            active(InboxItem)
            .where(InboxItem.processed_at.is_not(None))
            .where(InboxItem.reviewed_at.is_(None))
            .order_by(InboxItem.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def dismiss_inbox_item(db: Session, item: InboxItem) -> None:
    """Soft-delete an inbox item (clear it from the capture/review queue).

    This only hides the inbox row. Its ``ai_training_examples`` are accounting
    data with no FK back to the item, so they are deliberately left untouched
    (prime directive #4). Soft-deleting also frees the ``input_hash`` from the
    active partial unique index, so the same text can be re-submitted later.
    The caller is responsible for committing.
    """
    soft_delete(item)
    db.flush()


# --- Trash / restore (Sprint 7) --------------------------------------------


def list_deleted_inbox_items(db: Session, *, limit: int = 50) -> Sequence[InboxItem]:
    """Soft-deleted (dismissed) inbox items, most-recently-deleted first."""
    return (
        db.execute(
            deleted(InboxItem).order_by(InboxItem.deleted_at.desc()).limit(limit)
        )
        .scalars()
        .all()
    )


def get_deleted_inbox_item(db: Session, inbox_item_id: int) -> InboxItem | None:
    return db.execute(
        deleted(InboxItem).where(InboxItem.id == inbox_item_id)
    ).scalar_one_or_none()


def restore_inbox_item(db: Session, item: InboxItem) -> InboxItem:
    """Un-dismiss an inbox item.

    Fails with ``RestoreConflictError`` if its ``input_hash`` now collides with an
    active item (the same text was re-captured after dismissal) — the active
    partial unique index would reject it.
    """
    existing = db.execute(
        active(InboxItem).where(InboxItem.input_hash == item.input_hash)
    ).scalar_one_or_none()
    if existing is not None:
        raise RestoreConflictError(
            "This note was re-captured after it was dismissed; the active copy "
            "already represents it."
        )
    restore(item)
    db.flush()
    db.refresh(item)
    return item


def list_candidates(db: Session, inbox_item_id: int) -> Sequence[Task]:
    """Active candidate (and reviewed) tasks belonging to one inbox item."""
    return (
        db.execute(
            active(Task).where(Task.inbox_item_id == inbox_item_id).order_by(Task.id)
        )
        .scalars()
        .all()
    )
