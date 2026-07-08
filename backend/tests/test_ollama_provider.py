"""OllamaProvider wire format: what actually lands in the /api/chat payload."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ai.providers.ollama import OllamaProvider


class _CapturingResponse:
    """Minimal stand-in for httpx.Response: valid chat shape, never errors."""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"message": {"content": "ok"}}


def _complete_and_capture_payload(
    monkeypatch: pytest.MonkeyPatch, provider: OllamaProvider
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _CapturingResponse:
        captured.update(json)
        return _CapturingResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    provider.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="gemma4:e2b",
        temperature=0.0,
        max_tokens=64,
        response_mode="text",
    )
    return captured


def test_payload_carries_keep_alive_so_shared_gpu_frees_after_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OllamaProvider(base_url="http://ollama.test", keep_alive="2m")

    payload = _complete_and_capture_payload(monkeypatch, provider)

    assert payload["keep_alive"] == "2m"


def test_keep_alive_defaults_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(base_url="http://ollama.test")

    payload = _complete_and_capture_payload(monkeypatch, provider)

    from app.config import get_settings

    assert payload["keep_alive"] == get_settings().ollama_keep_alive
