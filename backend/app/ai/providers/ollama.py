from __future__ import annotations

from typing import Any

import httpx

from app.ai.providers.base import BaseProvider, Message, ResponseMode
from app.config import get_settings


class OllamaProvider(BaseProvider):
    """Talks to a local Ollama runtime over its HTTP API.

    Deliberately uses ``httpx`` against ``/api/chat`` rather than the ``ollama``
    Python package — the constitution forbids importing ``ollama`` outside this
    module, and keeping the wire format explicit makes the v2 llama.cpp swap a
    matter of adding a sibling provider, not rewriting workflows.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 120.0) -> None:
        self._base_url = (base_url or get_settings().ollama_base_url).rstrip("/")
        self._timeout = timeout

    def complete(
        self,
        *,
        messages: list[Message],
        model: str,
        temperature: float,
        max_tokens: int,
        response_mode: ResponseMode,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        # Ollama's structured-output support: pass a JSON Schema object as
        # `format` and the runtime constrains the response to it.
        if response_mode == "json_schema" and json_schema is not None:
            payload["format"] = json_schema

        response = httpx.post(
            f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
        )
        response.raise_for_status()
        data = response.json()
        return str(data["message"]["content"])
