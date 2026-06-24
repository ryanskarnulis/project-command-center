"""Gateway-level error handling: provider failures become GatewayError."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ai import gateway
from app.ai.providers.base import BaseProvider


class _RaisingProvider(BaseProvider):
    """A provider double whose completion always fails the given way."""

    exc: Exception = httpx.ConnectError("connection refused")

    def complete(self, **_: Any) -> str:
        raise self.exc


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        ValueError("unexpected Ollama response shape"),
    ],
)
def test_complete_wraps_provider_failures_as_gateway_error(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    provider = _RaisingProvider
    provider.exc = exc
    monkeypatch.setitem(gateway._PROVIDERS, "ollama", provider)

    with pytest.raises(gateway.GatewayError):
        gateway.complete(profile_name="task_extraction", user_content="hi")
