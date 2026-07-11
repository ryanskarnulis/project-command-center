"""llama.cpp provider: chat completions with tools against the shared runtime.

PCC's side of the workspace ``../llama-swap/`` stack (see
``docs/agent-design.md``, "Runtime"): OpenAI wire format spoken over plain
``httpx`` — no SDK — with every response validated by Pydantic wire models at
the boundary. Malformed server output, tool-call arguments that aren't JSON,
or structured output that fails its schema raise typed errors; nothing is
best-effort parsed.

Gemma quirks handled here (mirroring the chess app's production experience on
the same server): chain-of-thought arrives in a separate ``reasoning_content``
field and is never treated as answer text nor echoed back into history, and
thinking is toggled per request via ``chat_template_kwargs`` (default OFF —
fast tool calls). Self-correction retries on bad tool calls belong to the
agent loop (next checkout), which is why the errors carry the tool name.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from typing import Any, TypeVar

import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings

logger = structlog.get_logger(__name__)

# The negotiated gemma-4 sampling set. ../llama-swap/config.yaml carries the
# same values as server-side defaults, but the provider always sets them per
# request so a server-config drift never changes PCC's behavior.
_TEMPERATURE = 1.0
_TOP_P = 0.95
_TOP_K = 64


class ProviderError(Exception):
    """Base for everything a completion attempt can raise."""


class ProviderRequestError(ProviderError):
    """No usable HTTP response: connect/timeout failure or a non-200 status."""


class ProviderResponseError(ProviderError):
    """The server answered 200 but the body failed validation."""


class ToolCallArgumentsError(ProviderResponseError):
    """The model emitted tool-call arguments that aren't a JSON object."""

    def __init__(self, tool_name: str, detail: str) -> None:
        super().__init__(f"tool call {tool_name!r}: {detail}")
        self.tool_name = tool_name


class ToolSpec(BaseModel):
    """One callable tool: name, description, JSON Schema for the arguments."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolCall(BaseModel):
    """A tool call with its arguments already parsed from the wire's JSON string."""

    id: str
    name: str
    arguments: dict[str, Any]


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class _WireFunction(BaseModel):
    name: str
    arguments: str | None = None


class _WireToolCall(BaseModel):
    id: str = ""
    function: _WireFunction


class _WireMessage(BaseModel):
    content: str | None = None
    # Gemma's chain-of-thought channel: validated so unexpected shapes fail
    # loudly, but never surfaced as answer text and never sent back in history.
    reasoning_content: str | None = None
    tool_calls: list[_WireToolCall] = []


class _WireChoice(BaseModel):
    message: _WireMessage
    finish_reason: str | None = None


class _WireCompletion(BaseModel):
    choices: list[_WireChoice] = Field(min_length=1)
    usage: Usage | None = None


class ChatResult(BaseModel):
    """One validated completion turn. ``content`` is answer text only."""

    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str | None
    usage: Usage | None

    def to_message(self) -> dict[str, Any]:
        """This turn as an assistant message for the next request's history.

        Tool arguments are re-serialized to the wire's JSON-string form;
        ``reasoning_content`` deliberately never round-trips.
        """
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "type": "function",
                    "id": call.id,
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in self.tool_calls
            ]
        return message


def tool_result_message(tool_call_id: str, content: str) -> dict[str, Any]:
    """The ``role: tool`` message answering one :class:`ToolCall`."""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


TStructured = TypeVar("TStructured", bound=BaseModel)


class LlamaCppProvider:
    """Chat-completions client for the shared llama-server (behind llama-swap).

    Synchronous by design, matching the sync service layer and MCP server;
    FastAPI runs sync callers in worker threads. ``client`` is injectable for
    tests (``httpx.MockTransport``); otherwise the provider owns one.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 300.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        # One generous read timeout rather than a special-cased first request:
        # a cold load through llama-swap is ~100 s before the first byte
        # (docs/agent-design.md, "Runtime"), and warm calls never get near it.
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0)
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LlamaCppProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """One completion turn, optionally offering tools."""
        payload = self._payload(
            messages, tools=tools, enable_thinking=enable_thinking, max_tokens=max_tokens
        )
        return self._result(self._post(payload))

    def chat_structured(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        schema: type[TStructured],
        max_tokens: int | None = None,
    ) -> TStructured:
        """One completion constrained to ``schema`` via ``json_schema``.

        The server's grammar constraint guarantees syntax, not semantics —
        the content is still validated against ``schema`` before anything
        downstream sees it. Thinking stays off: the grammar constrains the
        whole output, which a thinking preamble would violate.
        """
        payload = self._payload(messages, tools=None, enable_thinking=False, max_tokens=max_tokens)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        }
        result = self._result(self._post(payload))
        if result.content is None:
            raise ProviderResponseError("structured completion returned no content")
        try:
            return schema.model_validate_json(result.content)
        except ValidationError as exc:
            raise ProviderResponseError(
                f"structured output failed {schema.__name__} validation: {exc}"
            ) from exc

    def _payload(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None,
        enable_thinking: bool,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "temperature": _TEMPERATURE,
            "top_p": _TOP_P,
            # llama-server accepts these OpenAI extensions as plain body
            # fields (no SDK extra_body indirection needed).
            "top_k": _TOP_K,
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [tool.to_wire() for tool in tools]
            payload["tool_choice"] = "auto"
        return payload

    def _post(self, payload: dict[str, Any]) -> _WireCompletion:
        log = logger.bind(llm_call_id=uuid.uuid4().hex[:8], model=self._model)
        log.info(
            "llm_request",
            messages=len(payload["messages"]),
            tools=len(payload.get("tools", ())),
            structured="response_format" in payload,
        )
        started = time.monotonic()
        try:
            response = self._client.post(f"{self._base_url}/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            log.error("llm_request_failed", error=str(exc))
            raise ProviderRequestError(f"llama-server request failed: {exc}") from exc
        duration_ms = round((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            log.error("llm_request_failed", status=response.status_code, duration_ms=duration_ms)
            raise ProviderRequestError(
                f"llama-server returned {response.status_code}: {response.text[:500]}"
            )
        try:
            completion = _WireCompletion.model_validate_json(response.text)
        except ValidationError as exc:
            log.error("llm_response_invalid", duration_ms=duration_ms, error=str(exc))
            raise ProviderResponseError(
                f"llama-server response failed wire validation: {exc}"
            ) from exc
        choice = completion.choices[0]
        log.info(
            "llm_response",
            duration_ms=duration_ms,
            finish_reason=choice.finish_reason,
            tool_calls=len(choice.message.tool_calls),
            prompt_tokens=completion.usage.prompt_tokens if completion.usage else None,
            completion_tokens=completion.usage.completion_tokens if completion.usage else None,
        )
        return completion

    @staticmethod
    def _result(completion: _WireCompletion) -> ChatResult:
        choice = completion.choices[0]
        calls: list[ToolCall] = []
        for wire_call in choice.message.tool_calls:
            raw = wire_call.function.arguments
            if raw is None or not raw.strip():
                arguments: Any = {}
            else:
                try:
                    arguments = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ToolCallArgumentsError(
                        wire_call.function.name, f"arguments are not valid JSON ({exc.msg})"
                    ) from exc
            if not isinstance(arguments, dict):
                raise ToolCallArgumentsError(
                    wire_call.function.name, "arguments are not a JSON object"
                )
            calls.append(
                ToolCall(id=wire_call.id, name=wire_call.function.name, arguments=arguments)
            )
        return ChatResult(
            content=choice.message.content,
            tool_calls=calls,
            finish_reason=choice.finish_reason,
            usage=completion.usage,
        )


def provider_from_settings() -> LlamaCppProvider:
    """The provider as configured (``LLAMACPP_*`` env / ``.env``)."""
    settings = get_settings()
    return LlamaCppProvider(
        settings.llamacpp_base_url,
        settings.llamacpp_model,
        timeout_seconds=settings.llamacpp_timeout_seconds,
    )
