import threading
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.api import conversation_locks, rate_limit
from app.db.models import Base
from app.db.session import enable_sqlite_fk_enforcement, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Generator[None, None, None]:
    # The limiter's hit store is process-global; clear it around every test so the
    # per-IP caps on the model routes don't bleed across cases (a file that hits a
    # rate-limited route many times would otherwise trip the limit cumulatively).
    rate_limit._reset()
    conversation_locks._reset()
    yield
    rate_limit._reset()
    conversation_locks._reset()


@pytest.fixture
def test_engine() -> Generator[Engine, None, None]:
    # StaticPool keeps a single in-memory connection so the schema is visible from
    # any thread (the TestClient runs handlers in a worker thread). Function-scoped
    # so each test gets a fresh database — no row leakage between tests.
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    enable_sqlite_fk_enforcement(engine)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Generator[Session, None, None]:
    TestingSessionLocal: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Concurrency + query-budget fixtures -------------------------------------
#
# ``test_engine`` above is ``:memory:`` + StaticPool: ONE shared DBAPI connection
# for the whole test, and ``client`` hands the test's own session back for every
# request. That is fine for behaviour tests and useless for concurrency ones —
# two "concurrent" sessions on it are literally the same transaction, so no race
# is reproducible. The fixtures below mirror production instead: a real file, WAL,
# and NullPool (a fresh connection per checkout).


@pytest.fixture
def file_engine(tmp_path: Path) -> Generator[Engine, None, None]:
    """File-backed WAL SQLite with production topology, for real concurrency tests."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrency.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    enable_sqlite_fk_enforcement(engine)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(file_engine: Engine) -> sessionmaker[Session]:
    """Independent sessions on ``file_engine`` — each gets its own connection."""
    return sessionmaker(autocommit=False, autoflush=False, bind=file_engine)


@pytest.fixture
def file_client(
    file_engine: Engine, session_factory: sessionmaker[Session]
) -> Generator[TestClient, None, None]:
    """TestClient over ``file_engine`` with a NEW session per request.

    Unlike ``client``, which yields the test's own session back every time, this
    reproduces the real per-request session lifecycle — required before any
    HTTP-level concurrency assertion means anything.
    """

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


Outcome = tuple[Any, BaseException | None]


@pytest.fixture
def race() -> Callable[..., tuple[Outcome, Outcome]]:
    """Run two callables in threads that rendezvous on a shared barrier.

    Each callable takes the ``threading.Barrier``; it does its read phase, calls
    ``barrier.wait()``, then does its write phase — which is what makes the
    interleaving deterministic instead of hopeful. Returns ``(result, exception)``
    per thread so the *test* decides which outcome is acceptable, rather than the
    race deciding for it.
    """

    def _run(
        first: Callable[[threading.Barrier], Any],
        second: Callable[[threading.Barrier], Any],
        *,
        timeout: float = 10.0,
    ) -> tuple[Outcome, Outcome]:
        barrier = threading.Barrier(2, timeout=timeout)
        outcomes: list[Outcome] = [(None, None), (None, None)]

        def invoke(index: int, fn: Callable[[threading.Barrier], Any]) -> None:
            try:
                outcomes[index] = (fn(barrier), None)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                outcomes[index] = (None, exc)

        threads = [
            threading.Thread(target=invoke, args=(0, first)),
            threading.Thread(target=invoke, args=(1, second)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout)
            assert not thread.is_alive(), "race thread deadlocked"
        return outcomes[0], outcomes[1]

    return _run


@pytest.fixture
def count_queries() -> Callable[[Engine], Any]:
    """Record every statement an engine executes, for query-budget assertions.

    Usage::

        with count_queries(engine) as statements:
            ...
        assert len(statements) <= 12
    """

    @contextmanager
    def _counter(engine: Engine) -> Iterator[list[str]]:
        statements: list[str] = []

        def record(
            _conn: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            yield statements
        finally:
            event.remove(engine, "before_cursor_execute", record)

    return _counter
