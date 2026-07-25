"""The rate-limit factory is retained for future agent endpoints but no route
uses it yet, so it's exercised in isolation on a throwaway app rather than
through a real endpoint."""

from collections.abc import Generator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import rate_limit


class _StubSettings:
    """Minimal stand-in exposing the per-minute attr the factory reads live."""

    rate_limit_test_per_min = 2


def _app_with_limited_route(bucket: str, path: str) -> FastAPI:
    app = FastAPI()

    @app.get(
        path,
        dependencies=[
            Depends(rate_limit.rate_limit(bucket, per_min_attr="rate_limit_test_per_min"))
        ],
    )
    def _handler() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def stub_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setattr(rate_limit, "get_settings", lambda: _StubSettings())
    rate_limit._reset()
    yield
    rate_limit._reset()


def test_route_throttles_after_limit(stub_settings: None) -> None:
    client = TestClient(_app_with_limited_route("ping", "/ping"))

    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200

    resp = client.get("/ping")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_buckets_are_independent(stub_settings: None) -> None:
    app = FastAPI()

    @app.get(
        "/a",
        dependencies=[
            Depends(rate_limit.rate_limit("a", per_min_attr="rate_limit_test_per_min"))
        ],
    )
    def _a() -> dict[str, bool]:
        return {"ok": True}

    @app.get(
        "/b",
        dependencies=[
            Depends(rate_limit.rate_limit("b", per_min_attr="rate_limit_test_per_min"))
        ],
    )
    def _b() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    # Exhaust bucket "a".
    client.get("/a")
    client.get("/a")
    assert client.get("/a").status_code == 429

    # Bucket "b" keeps its own independent counter.
    assert client.get("/b").status_code == 200


class _DegenerateSettings:
    """A limit Settings now rejects, to prove the limiter still degrades safely."""

    rate_limit_test_per_min = 0


@pytest.mark.parametrize("limit", [0, -1])
def test_degenerate_limit_returns_429_not_indexerror(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    """Issue #165: an empty deque used to raise IndexError on the 429 path."""
    settings = _DegenerateSettings()
    settings.rate_limit_test_per_min = limit
    monkeypatch.setattr(rate_limit, "get_settings", lambda: settings)
    rate_limit._reset()

    client = TestClient(_app_with_limited_route("degenerate", "/degenerate"))

    resp = client.get("/degenerate")

    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    rate_limit._reset()
