from collections.abc import Generator
from contextvars import ContextVar

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings

# Whether transactions started in this context should take the write lock up
# front. ``get_db_write`` sets ``begin_statement`` on its own Connection and does
# not need this; it exists for callers that don't own the Connection — the MCP /
# agent tool session, which builds its Session from a swappable factory. A
# ContextVar rather than a global because two tool calls can be in flight in
# different threads of the same process.
begin_immediate: ContextVar[bool] = ContextVar("begin_immediate", default=False)


def enable_sqlite_fk_enforcement(engine: Engine) -> None:
    """Configure a SQLite engine's pragmas and transaction control (no-op elsewhere).

    Pragmas, per connection:

    - ``foreign_keys = ON``: SQLite ignores foreign keys unless this is issued on
      each connection, so orphaned FKs persist silently without it.
    - ``journal_mode = WAL``: the default rollback journal blocks readers during
      any write; WAL lets concurrent clients read while a write is in flight
      without "database is locked" errors. Persistent, but cheap to re-issue.
    - ``busy_timeout``: when two writers do collide, wait for the lock instead of
      failing immediately.

    Transaction control: pysqlite's legacy mode emits ``BEGIN`` only before DML
    and never before a ``SELECT``, so a read-then-write sequence is not one
    transaction — the read's answer can be stale by the time the write lands.
    Every check-then-act invariant in the service layer sits in that gap: the
    recurrence idempotency guard, ``_would_cycle``, the completion gate,
    ``ensure_default_project_id``. Setting ``isolation_level = None`` hands
    ``BEGIN`` to SQLAlchemy, and the ``begin`` listener then emits it — the
    listener is mandatory, because without it ``Connection.begin()`` becomes a
    DBAPI no-op and the session silently runs in autocommit.

    Handing over ``BEGIN`` is only half of it: a DEFERRED transaction that reads
    first and writes later still has to upgrade its lock, and if another
    connection committed in between SQLite rejects it with ``SQLITE_BUSY_SNAPSHOT``
    — which ``busy_timeout`` does *not* retry, because it is a stale-snapshot
    rejection rather than a lock wait. Writers therefore have to start
    ``IMMEDIATE``; see ``get_db_write``.

    Gated on the dialect so it stays correct if ``database_url`` is ever
    non-SQLite. NullPool (below) opens a connection per checkout, so these run
    per request — all fast no-ops once set.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.close()
        dbapi_conn.isolation_level = None  # type: ignore[attr-defined]

    @event.listens_for(engine, "begin")
    def _emit_begin(conn: Connection) -> None:
        statement = conn.info.get("begin_statement") or (
            "BEGIN IMMEDIATE" if begin_immediate.get() else "BEGIN"
        )
        conn.exec_driver_sql(statement)


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
    """Read session: a DEFERRED transaction, so a request reads one snapshot.

    WAL readers never block writers, so this costs nothing, and NullPool discards
    the connection at close rather than parking a pinned snapshot. Write routes
    must depend on ``get_db_write`` instead — ``test_write_routes_use_get_db_write``
    enforces that.
    """
    db: Session = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_write() -> Generator[Session, None, None]:
    """Write session: ``BEGIN IMMEDIATE``, so read-then-write invariants hold.

    The write lock is taken when the transaction starts rather than at its first
    write, so only one writer is ever inside a write transaction — across threads
    *and* across processes, which matters because the MCP server runs separately
    from uvicorn and an in-process lock could never cover it. The loser's
    transaction begins only after the winner commits, so its check-then-act guard
    SEES the winner's row and does the right thing: the recurrence guard returns
    the existing occurrence, the duplicate-dependency check raises, and the cycle
    check rejects. Without this they all read pre-commit state and act on it.

    ``BEGIN IMMEDIATE`` has to be set on the Connection before the Session opens a
    transaction on it, which is why this binds an explicit Connection rather than
    letting the Session check one out lazily.
    """
    conn = engine.connect()
    conn.info["begin_statement"] = "BEGIN IMMEDIATE"
    db: Session = SessionLocal(bind=conn)
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        # Session.close() releases the Session's hold but not a Connection that
        # was handed to it — without this the connection leaks for the process
        # lifetime, and with NullPool that is a real file handle each time.
        conn.close()
