from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, InboxItem, InboxSource
from app.services import inbox as inbox_service
from app.services.common import active, soft_delete


def test_hash_text_is_deterministic() -> None:
    assert inbox_service.hash_text("hello") == inbox_service.hash_text("hello")
    assert inbox_service.hash_text("hello") != inbox_service.hash_text("world")


def test_list_inbox_route_limit_and_offset_page_the_result(
    client: TestClient, db_session: Session
) -> None:
    created = [
        inbox_service.create_inbox_item(db_session, raw_text=f"note {i}")
        for i in range(5)
    ]
    db_session.commit()
    ids = [item.id for item in created]

    first = client.get("/api/inbox?limit=2")
    assert first.status_code == 200
    assert [r["id"] for r in first.json()] == ids[:2]

    second = client.get("/api/inbox?limit=2&offset=2")
    assert second.status_code == 200
    assert [r["id"] for r in second.json()] == ids[2:4]


def test_list_inbox_route_rejects_out_of_range_limit(client: TestClient) -> None:
    assert client.get("/api/inbox?limit=0").status_code == 422
    assert client.get("/api/inbox?limit=501").status_code == 422
    assert client.get("/api/inbox?offset=-1").status_code == 422


def test_create_inbox_item_is_idempotent(db_session: Session) -> None:
    first = inbox_service.create_inbox_item(db_session, raw_text="finish the audit")
    db_session.commit()
    second = inbox_service.create_inbox_item(db_session, raw_text="finish the audit")
    db_session.commit()

    assert first.id == second.id
    assert first.input_hash == inbox_service.hash_text("finish the audit")

    rows = db_session.execute(active(InboxItem)).scalars().all()
    assert len(rows) == 1


def test_create_inbox_item_distinct_text_creates_new_row(db_session: Session) -> None:
    a = inbox_service.create_inbox_item(db_session, raw_text="task one")
    db_session.commit()
    b = inbox_service.create_inbox_item(
        db_session, raw_text="task two", source=InboxSource.discord
    )
    db_session.commit()

    assert a.id != b.id
    assert b.source == InboxSource.discord
    assert len(db_session.execute(active(InboxItem)).scalars().all()) == 2


def test_create_inbox_item_allows_resubmission_after_soft_delete(
    db_session: Session,
) -> None:
    first = inbox_service.create_inbox_item(db_session, raw_text="dismissed note")
    db_session.commit()

    first_id = first.id
    soft_delete(first)
    db_session.commit()

    second = inbox_service.create_inbox_item(db_session, raw_text="dismissed note")
    db_session.commit()

    assert second.id != first_id
    assert second.input_hash == first.input_hash
    assert len(db_session.execute(active(InboxItem)).scalars().all()) == 1


@pytest.fixture
def file_backed_sessions(
    tmp_path: Path,
) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(f"sqlite:///{tmp_path / 'inbox-race.db'}")
    Base.metadata.create_all(bind=engine)
    SessionLocal: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )
    try:
        yield SessionLocal
    finally:
        engine.dispose()


def test_create_inbox_item_handles_unique_index_race(
    file_backed_sessions: sessionmaker[Session],
) -> None:
    raw_text = "same note from web and discord"
    first_session = file_backed_sessions()
    second_session = file_backed_sessions()
    did_insert_competing_row = False

    def insert_competing_row(
        session: Session, _flush_context: object, _instances: object
    ) -> None:
        nonlocal did_insert_competing_row
        if did_insert_competing_row:
            return
        did_insert_competing_row = True
        winner = inbox_service.create_inbox_item(second_session, raw_text=raw_text)
        second_session.commit()
        assert winner.id is not None

    event.listen(first_session, "before_flush", insert_competing_row)

    try:
        result = inbox_service.create_inbox_item(first_session, raw_text=raw_text)
        first_session.commit()

        rows = first_session.execute(select(InboxItem)).scalars().all()
        assert len(rows) == 1
        assert result.id == rows[0].id
        assert result.input_hash == inbox_service.hash_text(raw_text)
    finally:
        event.remove(first_session, "before_flush", insert_competing_row)
        first_session.close()
        second_session.close()
