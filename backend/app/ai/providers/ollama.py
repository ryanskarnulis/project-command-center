from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from app.ai.providers.base import BaseProvider, Message, ResponseMode
from app.config import get_settings

logger = structlog.get_logger(__name__)


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

        logger.info(
            "ollama_request",
            model=model,
            message_count=len(messages),
            structured=response_mode == "json_schema" and json_schema is not None,
            max_tokens=max_tokens,
        )
        start = time.perf_counter()
        try:
            response = httpx.post(
                f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            status_code = exc.response.status_code if isinstance(
                exc, httpx.HTTPStatusError
            ) else None
            logger.error(
                "ollama_request_failed",
                model=model,
                error=str(exc),
                status_code=status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
            )
            raise

        data = response.json()
        try:
            content = str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            logger.error(
                "ollama_response_malformed",
                model=model,
                keys=list(data.keys()) if isinstance(data, dict) else None,
            )
            raise ValueError("unexpected Ollama response shape") from exc

        logger.info(
            "ollama_response",
            model=model,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
            response_chars=len(content),
        )
        return content
