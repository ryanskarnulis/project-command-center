from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


def enable_sqlite_fk_enforcement(engine: Engine) -> None:
    """Turn on per-connection FK enforcement for SQLite engines (no-op otherwise).

    SQLite ignores foreign keys unless ``PRAGMA foreign_keys = ON`` is issued on
    each connection, so orphaned FKs persist silently without this. Gated on the
    dialect so it stays correct if ``database_url`` is ever non-SQLite.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


def _build_engine() -> Engine:
    settings = get_settings()
    kwargs: dict[str, object] = {}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        # NullPool: open a fresh connection per checkout and discard it on close,
        # rather than parking it in the default QueuePool. A pooled SQLite
        # connection whose implicit read transaction was never committed (a
        # read-only request just closes its session) pins a stale read snapshot,
        # so later requests reusing it never see another connection's committed
        # writes — the app served frozen, pre-edit task data. A per-request
        # connection always reads the latest committed disk state. Cost is
        # negligible for a local single-file SQLite app.
        kwargs["poolclass"] = NullPool
    return create_engine(settings.database_url, **kwargs)


engine: Engine = _build_engine()
enable_sqlite_fk_enforcement(engine)

SessionLocal: sessionmaker[Session] = sessionmaker(
    autocommit=False, autoflush=False, bind=engine
)


def get_db() -> Generator[Session, None, None]:
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
