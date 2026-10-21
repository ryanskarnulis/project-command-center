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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError

from app.ai.context_capture import CallStamp, ContextCapture, context_capture_from_settings
from app.config import get_settings

logger = structlog.get_logger(__name__)

# The negotiated gemma-4 sampling set. ../llama-swap/config.yaml carries the
# same values as server-side defaults, but the provider always sets them per
# request so a server-config drift never changes PCC's behavior.
_TEMPERATURE = 1.0
_TOP_P = 0.95
_TOP_K = 64
_CONNECT_TIMEOUT_SECONDS = 10.0
# How long the context capture (#401) waits for llama-server to render a
# call's template. Rendering is string work with no generation behind it, so
# this is a ceiling for a server that went away, never a wait anyone meets.
_TEMPLATE_TIMEOUT_SECONDS = 5.0


class ProviderError(Exception):
    """Base for everything a completion attempt can raise."""


class ProviderRequestError(ProviderError):
    """No usable HTTP response: connect/timeout failure or a non-200 status."""


class ProviderResponseError(ProviderError):
    """The server answered 200 but the body failed validation."""


class ToolCallArgumentsError(ProviderResponseError):
    """The model emitted tool-call arguments that aren't a JSON object.

    The server did answer, so the call's cost is known: ``usage`` and
    ``server`` carry it for the run trace (#400), ``None`` when not given.
    """

    def __init__(
        self,
        tool_name: str,
        detail: str,
        *,
        usage: Usage | None = None,
        server: ServerStamp | None = None,
    ) -> None:
        super().__init__(f"tool call {tool_name!r}: {detail}")
        self.tool_name = tool_name
        self.usage = usage
        self.server = server


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


class PromptTokensDetails(BaseModel):
    cached_tokens: int | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # llama-server reports the prompt tokens it reused from its KV cache here.
    prompt_tokens_details: PromptTokensDetails | None = None

    @property
    def cached_tokens(self) -> int | None:
        details = self.prompt_tokens_details
        return None if details is None else details.cached_tokens


class _WireTimings(BaseModel):
    """llama-server's own clock for one call (a non-OpenAI extension)."""

    prompt_ms: float | None = None
    predicted_ms: float | None = None


class ServerStamp(BaseModel):
    """The server's own account of one call, for the run trace (#400).

    ``model`` is what the server says it ran (the weights behind the alias),
    ``fingerprint`` its build, ``server_ms`` its prompt plus generation time —
    the call's wall clock minus this is queueing, transport and any cold load.
    Every field is ``None`` when the server did not say.
    """

    model: str | None = None
    fingerprint: str | None = None
    server_ms: int | None = None


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
    model: str | None = None
    system_fingerprint: str | None = None
    timings: _WireTimings | None = None

    def stamp(self) -> ServerStamp:
        server_ms: int | None = None
        if self.timings is not None and (
            self.timings.prompt_ms is not None or self.timings.predicted_ms is not None
        ):
            server_ms = round((self.timings.prompt_ms or 0.0) + (self.timings.predicted_ms or 0.0))
        return ServerStamp(
            model=self.model, fingerprint=self.system_fingerprint, server_ms=server_ms
        )


class ChatResult(BaseModel):
    """One validated completion turn. ``content`` is answer text only."""

    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str | None
    usage: Usage | None
    # The server's account of the call (#400); never sent back in history.
    server: ServerStamp | None = None

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
TParsed = TypeVar("TParsed")


@dataclass
class _Wire:
    """How far one captured call got: the request once built, the response
    once it arrived (#401)."""

    request: httpx.Request | None = None
    response: httpx.Response | None = None


class LlamaCppProvider:
    """Chat-completions client for the shared llama-server (behind llama-swap).

    Synchronous by design, matching the sync service layer and MCP server;
    FastAPI runs sync callers in worker threads. ``client`` is injectable for
    tests (``httpx.MockTransport``); otherwise the provider owns one.

    ``capture``, when given, keeps every call's exact request and response
    bytes and the prompt the server's chat template rendered from them
    (:mod:`app.ai.context_capture`, #401). ``None`` — the default — sends one
    request per call and records nothing.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 300.0,
        client: httpx.Client | None = None,
        capture: ContextCapture | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._capture = capture
        # llama-swap's root: ``/upstream/<model>/apply-template`` hangs off it,
        # not off the OpenAI ``/v1`` prefix.
        self._root = self._base_url.removesuffix("/v1")
        # One generous read timeout rather than a special-cased first request:
        # a cold load through llama-swap is ~100 s before the first byte
        # (docs/agent-design.md, "Runtime"), and warm calls never get near it.
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                timeout_seconds, connect=min(_CONNECT_TIMEOUT_SECONDS, timeout_seconds)
            )
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LlamaCppProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def model(self) -> str:
        """The model alias every request names (what the run trace records)."""
        return self._model

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        """One completion turn, optionally offering tools.

        ``timeout`` overrides the client's default read timeout for this one
        call (the agent loop passes the time left in its run budget), so a
        single call can never outlive the caller's deadline.
        """
        payload = self._payload(
            messages, tools=tools, enable_thinking=enable_thinking, max_tokens=max_tokens
        )
        return self._post(payload, self._result, timeout=timeout)

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

        def parse(completion: _WireCompletion) -> TStructured:
            result = self._result(completion)
            if result.content is None:
                raise ProviderResponseError("structured completion returned no content")
            try:
                return schema.model_validate_json(result.content)
            except ValidationError as exc:
                raise ProviderResponseError(
                    f"structured output failed {schema.__name__} validation: {exc}"
                ) from exc

        return self._post(payload, parse)

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

    def _post(
        self,
        payload: dict[str, Any],
        parse: Callable[[_WireCompletion], TParsed],
        *,
        timeout: float | None = None,
    ) -> TParsed:
        """Send one completion request and ``parse`` its validated body.

        ``parse`` runs inside the call so the context capture (#401) records
        whatever typed error the call ends in — a bad tool-call argument or a
        failed structured schema included — beside the bytes behind it.
        """
        stamp = self._begin_capture()
        if stamp is None:
            return parse(self._exchange(payload, timeout, None))
        started = time.monotonic()
        wire = _Wire()
        error: Exception | None = None
        try:
            return parse(self._exchange(payload, timeout, wire))
        except Exception as exc:
            error = exc
            raise
        finally:
            self._capture_call(stamp, wire, error, started)

    def _exchange(
        self,
        payload: dict[str, Any],
        timeout: float | None,
        wire: _Wire | None,
    ) -> _WireCompletion:
        """One request/response round trip, validated against the wire models.

        With ``wire`` given (capture on), the request is kept on it before it
        goes out and the response as soon as it arrives, so the capture holds
        however far the call got, however it ended.
        """
        log = logger.bind(llm_call_id=uuid.uuid4().hex[:8], model=self._model)
        log.info(
            "llm_request",
            messages=len(payload["messages"]),
            tools=len(payload.get("tools", ())),
            structured="response_format" in payload,
        )
        # Per-call timeout override: every phase — connect included — is capped
        # at the caller's remaining budget, so the request cannot outlive the
        # agent's wall-clock deadline. httpx.USE_CLIENT_DEFAULT falls back to
        # the timeout set at construction.
        request_timeout: Any = (
            httpx.USE_CLIENT_DEFAULT
            if timeout is None
            else httpx.Timeout(timeout, connect=min(_CONNECT_TIMEOUT_SECONDS, timeout))
        )
        # Built once and then sent — exactly what ``post(json=...)`` does, so
        # the bytes on the wire are unchanged — which makes ``request.content``
        # literally what went out: what the context capture keeps.
        request = self._client.build_request(
            "POST",
            f"{self._base_url}/chat/completions",
            json=payload,
            timeout=request_timeout,
        )
        if wire is not None:
            wire.request = request
        started = time.monotonic()
        try:
            response = self._client.send(request)
        except httpx.HTTPError as exc:
            log.error("llm_request_failed", error=str(exc))
            raise ProviderRequestError(f"llama-server request failed: {exc}") from exc
        if wire is not None:
            wire.response = response
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

    def _begin_capture(self) -> CallStamp | None:
        if self._capture is None:
            return None
        try:
            return self._capture.begin()
        except Exception as exc:
            logger.warning(
                "context_capture_failed", error=str(exc), error_type=type(exc).__name__
            )
            return None

    def _capture_call(
        self,
        stamp: CallStamp,
        wire: _Wire,
        error: Exception | None,
        started: float,
    ) -> None:
        """Hand one finished call to the capture. Never raises: the call's own
        outcome — a result or its typed error — is what the run gets."""
        assert self._capture is not None
        try:
            request, response = wire.request, wire.response
            self._capture.record(
                stamp,
                {
                    "url": None if request is None else str(request.url),
                    "ms": round((time.monotonic() - started) * 1000),
                    "request": None if request is None else _body_text(request.content),
                    "status_code": None if response is None else response.status_code,
                    "response": None if response is None else _body_text(response.content),
                    "error": "" if error is None else f"{type(error).__name__}: {error}",
                    # A server that never answered cannot render either; asking
                    # would only stall a run that is already failing.
                    "template": None
                    if request is None or response is None
                    else self._render_template(request.content),
                },
            )
        except Exception as exc:
            logger.warning(
                "context_capture_failed", error=str(exc), error_type=type(exc).__name__
            )

    def _render_template(self, body: bytes) -> dict[str, str]:
        """The prompt string llama-server's chat template renders from ``body``.

        Asked of the server rather than re-rendered here: the text the model
        tokenized is whatever *its* ``--jinja`` template makes of these
        messages and tools, and a copy of the template in this repo would be a
        second truth that drifts. The same bytes go to ``apply-template`` as
        went to ``/chat/completions``. llama-swap routes it only under
        ``/upstream/<model>/`` (``/v1/apply-template`` is a 404).
        """
        try:
            response = self._client.post(
                f"{self._root}/upstream/{self._model}/apply-template",
                content=body,
                headers={"content-type": "application/json"},
                timeout=_TEMPLATE_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}: {response.text[:500]}"}
            prompt = response.json().get("prompt")
            if not isinstance(prompt, str):
                return {"error": "apply-template answered without a prompt string"}
            return {"prompt": prompt}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

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
                        wire_call.function.name,
                        f"arguments are not valid JSON ({exc.msg})",
                        usage=completion.usage,
                        server=completion.stamp(),
                    ) from exc
            if not isinstance(arguments, dict):
                raise ToolCallArgumentsError(
                    wire_call.function.name,
                    "arguments are not a JSON object",
                    usage=completion.usage,
                    server=completion.stamp(),
                )
            calls.append(
                ToolCall(id=wire_call.id, name=wire_call.function.name, arguments=arguments)
            )
        return ChatResult(
            content=choice.message.content,
            tool_calls=calls,
            finish_reason=choice.finish_reason,
            usage=completion.usage,
            server=completion.stamp(),
        )


def provider_from_settings() -> LlamaCppProvider:
    """The provider as configured (``LLAMACPP_*`` env / ``.env``), capturing
    every call's bytes when ``PCC_CONTEXT_PATH`` is set (#401)."""
    settings = get_settings()
    return LlamaCppProvider(
        settings.llamacpp_base_url,
        settings.llamacpp_model,
        timeout_seconds=settings.llamacpp_timeout_seconds,
        capture=context_capture_from_settings(),
    )


def _body_text(body: bytes) -> str:
    """A wire body as the text it is. llama-server speaks UTF-8 both ways; a
    body that is not is kept with its bad bytes replaced rather than lost."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")
