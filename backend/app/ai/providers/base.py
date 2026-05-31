from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, TypedDict

ResponseMode = Literal["json_schema", "text"]


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class BaseProvider(ABC):
    """A model provider. Returns raw assistant text and nothing more.

    Parsing and validation happen in the workflow layer, never here — the
    provider's only job is to talk to a model runtime and hand back a string.
    """

    @abstractmethod
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
        """Return the raw assistant text for ``messages``.

        ``json_schema`` is only meaningful when ``response_mode`` is
        ``"json_schema"``; providers that support structured output should
        constrain the response to it.
        """
        ...
