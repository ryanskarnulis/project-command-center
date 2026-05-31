from sqlalchemy.orm import Session

from app.db.models import InboxSource
from app.services import inbox as inbox_service
from app.services.common import active
from app.db.models import InboxItem


def test_hash_text_is_deterministic() -> None:
    assert inbox_service.hash_text("hello") == inbox_service.hash_text("hello")
    assert inbox_service.hash_text("hello") != inbox_service.hash_text("world")


def test_create_inbox_item_is_idempotent(db_session: Session) -> None:
    first = inbox_service.create_inbox_item(db_session, raw_text="finish the audit")
    second = inbox_service.create_inbox_item(db_session, raw_text="finish the audit")

    assert first.id == second.id
    assert first.input_hash == inbox_service.hash_text("finish the audit")

    rows = db_session.execute(active(InboxItem)).scalars().all()
    assert len(rows) == 1


def test_create_inbox_item_distinct_text_creates_new_row(db_session: Session) -> None:
    a = inbox_service.create_inbox_item(db_session, raw_text="task one")
    b = inbox_service.create_inbox_item(
        db_session, raw_text="task two", source=InboxSource.discord
    )

    assert a.id != b.id
    assert b.source == InboxSource.discord
    assert len(db_session.execute(active(InboxItem)).scalars().all()) == 2
