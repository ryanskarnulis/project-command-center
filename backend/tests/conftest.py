from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import rate_limit
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
    yield
    rate_limit._reset()


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
