from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


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

SessionLocal: sessionmaker[Session] = sessionmaker(
    autocommit=False, autoflush=False, bind=engine
)


def get_db() -> Generator[Session, None, None]:
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
