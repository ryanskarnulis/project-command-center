from __future__ import annotations

import pytest
from starlette.requests import Request

from app.api import request_ip
from app.config import Settings


def make_request(host: str | None, headers: dict[str, str] | None = None) -> Request:
    """Build a minimal ASGI request with a given peer and headers, no app needed."""
    scope: dict[str, object] = {
        "type": "http",
        "client": (host, 12345) if host is not None else None,
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
    }
    return Request(scope)


@pytest.fixture
def trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trust a single proxy IP for resolution."""
    settings = Settings(trusted_proxy_ips="10.9.9.9")
    monkeypatch.setattr(request_ip, "get_settings", lambda: settings)


# --- resolve_client_ip (rate-limiter key) ----------------------------------


def test_no_trust_returns_direct_peer_and_ignores_xff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request_ip, "get_settings", lambda: Settings())
    req = make_request("192.168.1.50", {"X-Forwarded-For": "127.0.0.1"})
    assert request_ip.resolve_client_ip(req) == "192.168.1.50"


def test_trusted_proxy_uses_rightmost_forwarded_entry(trusted: None) -> None:
    # The rightmost entry is the address nginx actually observed; the leftmost is
    # client-supplied and forgeable, so it must never be the key.
    req = make_request("10.9.9.9", {"X-Forwarded-For": "127.0.0.1, 172.28.0.5"})
    assert request_ip.resolve_client_ip(req) == "172.28.0.5"


def test_untrusted_peer_spoofing_xff_is_ignored(trusted: None) -> None:
    # The peer is NOT the trusted proxy, so its forged header must not win.
    req = make_request("192.168.1.50", {"X-Forwarded-For": "127.0.0.1"})
    assert request_ip.resolve_client_ip(req) == "192.168.1.50"


def test_trusted_proxy_without_header_falls_back_to_peer(trusted: None) -> None:
    req = make_request("10.9.9.9")
    assert request_ip.resolve_client_ip(req) == "10.9.9.9"


def test_missing_client_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_ip, "get_settings", lambda: Settings())
    assert request_ip.resolve_client_ip(make_request(None)) is None


# --- is_trusted_proxy -------------------------------------------------------


def test_is_trusted_proxy_matches_cidr(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(trusted_proxy_ips="172.28.0.0/16")
    monkeypatch.setattr(request_ip, "get_settings", lambda: settings)
    assert request_ip.is_trusted_proxy("172.28.5.7") is True
    assert request_ip.is_trusted_proxy("192.168.1.50") is False


def test_is_trusted_proxy_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_ip, "get_settings", lambda: Settings())
    assert request_ip.is_trusted_proxy("172.28.5.7") is False


def test_unparseable_trusted_entry_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(trusted_proxy_ips="not-an-ip")
    monkeypatch.setattr(request_ip, "get_settings", lambda: settings)
    assert request_ip.is_trusted_proxy("10.9.9.9") is False


# --- proxy_is_host_only -----------------------------------------------------


@pytest.mark.parametrize(
    ("bind", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("192.168.1.10", False),
        ("garbage", False),
    ],
)
def test_proxy_is_host_only(
    monkeypatch: pytest.MonkeyPatch, bind: str, expected: bool
) -> None:
    settings = Settings(frontend_bind=bind)
    monkeypatch.setattr(request_ip, "get_settings", lambda: settings)
    assert request_ip.proxy_is_host_only() is expected
