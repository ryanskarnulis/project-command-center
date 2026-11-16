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
fast tool calls).

What changes with the model — sampling, the thinking kwarg's name, the
per-call token caps — is the model's profile (:mod:`app.ai.model_profile`,
#402), picked by the model name. The provider sets the profile's sampling on
every request, so a drift in ``../llama-swap/config.yaml``'s server-side
defaults never changes PCC's behavior. Self-correction retries on bad tool calls belong to the
agent loop (next checkout), which is why the errors carry the tool name.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
import structlog
from pydantic import BaseModel, ValidationError

# Re-exported: the fleet's chat wire, vendored (agent-standard/agentkit).
from app.ai.agentkit.chat import (
    ChatResult as ChatResult,
    ProviderError as ProviderError,
    ProviderFailure as ProviderFailure,
    ProviderRequestError as ProviderRequestError,
    ProviderResponseError as ProviderResponseError,
    ServerStamp as ServerStamp,
    StreamedTurn,
    ToolCall as ToolCall,
    ToolCallArgumentsError as ToolCallArgumentsError,
    ToolSpec as ToolSpec,
    Usage as Usage,
    WireCompletion,
    bad_status,
    body_text,
    chat_result,
    parse_completion,
    request_failed,
    tool_result_message as tool_result_message,
)
from app.ai.context_capture import CallStamp, ContextCapture, context_capture_from_settings
from app.ai.model_profile import ModelProfile, load_profile
from app.config import get_settings

logger = structlog.get_logger(__name__)

_CONNECT_TIMEOUT_SECONDS = 10.0
# How long the context capture (#401) waits for llama-server to render a
# call's template. Rendering is string work with no generation behind it, so
# this is a ceiling for a server that went away, never a wait anyone meets.
_TEMPLATE_TIMEOUT_SECONDS = 5.0


TStructured = TypeVar("TStructured", bound=BaseModel)
TParsed = TypeVar("TParsed")


@dataclass
class _Wire:
    """How far one captured call got: the request once built, the response
    once it arrived (#401). ``streamed`` is a streamed call's body as read so
    far (#516), the event-stream lines as they came; ``None`` for a plain call."""

    request: httpx.Request | None = None
    response: httpx.Response | None = None
    streamed: list[str] | None = None


class LlamaCppProvider:
    """Chat-completions client for the shared llama-server (behind llama-swap).

    Synchronous by design, matching the sync service layer and MCP server;
    FastAPI runs sync callers in worker threads. ``client`` is injectable for
    tests (``httpx.MockTransport``); otherwise the provider owns one.

    ``capture``, when given, keeps every call's exact request and response
    bytes and the prompt the server's chat template rendered from them
    (:mod:`app.ai.context_capture`, #401). ``None`` — the default — sends one
    request per call and records nothing.

    ``profile`` is the model's (#402); ``None`` — the default — loads the one
    named for ``model``, or the default profile with a warning when none is.

    ``seed``, when given, is sent as llama-server's sampling ``seed`` on every
    call, so two providers with one seed draw the same sampling stream — the
    eval gate's paired arms (#403). ``None`` — the default, and all the app
    ever uses — sends no ``seed`` field at all, so the request bytes are what
    they were (``tests/test_request_bytes.py``).
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 300.0,
        client: httpx.Client | None = None,
        capture: ContextCapture | None = None,
        profile: ModelProfile | None = None,
        seed: int | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._profile = profile if profile is not None else load_profile(model)
        self._capture = capture
        self._seed = seed
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

    @property
    def profile(self) -> ModelProfile:
        """The model's profile: its sampling, thinking kwarg and token caps."""
        return self._profile

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
        timeout: float | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        """One completion turn, optionally offering tools.

        ``timeout`` overrides the client's default read timeout for this one
        call (the agent loop passes the time left in its run budget), so a
        single call can never outlive the caller's deadline. ``temperature``
        overrides the profile's for this one call (the planner's, #418).
        """
        payload = self._payload(
            messages,
            tools=tools,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return self._post(payload, chat_result, timeout=timeout)

    def peek(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[ToolSpec] | None = None,
        enable_thinking: bool = False,
        max_tokens: int | None = None,
        timeout: float | None = None,
        temperature: float | None = None,
    ) -> ChatResult | None:
        """:meth:`chat`, streamed, stopped as soon as the turn shows it is
        only words (#516; music's ``peek``, music#97).

        The same request as :meth:`chat`, sent with ``stream: true``. The
        first meaningful delta decides: plain content (the model has begun its
        closing line) closes the stream and returns ``None``; a tool call is
        read to the end and returned exactly as :meth:`chat` would return the
        turn, so a run that goes on working goes on unchanged. A stream that
        ends with no meaningful delta (an empty text turn) is ``None`` too.
        Errors are :meth:`chat`'s: a failed request or a non-200 is a
        :class:`ProviderRequestError`, a chunk that is not JSON a
        :class:`ProviderResponseError`, a tool call's bad arguments a
        :class:`ToolCallArgumentsError`. The context capture (#401) keeps the
        request bytes and the event-stream lines read, up to where it stopped.
        """
        payload = self._payload(
            messages,
            tools=tools,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        stamp = self._begin_capture()
        started = time.monotonic()
        wire = _Wire(streamed=[])
        error: Exception | None = None
        try:
            return self._stream(payload, timeout, wire)
        except Exception as exc:
            error = exc
            raise
        finally:
            if stamp is not None:
                self._capture_call(stamp, wire, error, started)

    def _stream(
        self, payload: dict[str, Any], timeout: float | None, wire: _Wire
    ) -> ChatResult | None:
        log = logger.bind(llm_call_id=uuid.uuid4().hex[:8], model=self._model)
        log.info(
            "llm_peek",
            messages=len(payload["messages"]),
            tools=len(payload.get("tools", ())),
        )
        request_timeout: Any = (
            httpx.USE_CLIENT_DEFAULT
            if timeout is None
            else httpx.Timeout(timeout, connect=min(_CONNECT_TIMEOUT_SECONDS, timeout))
        )
        request = self._client.build_request(
            "POST",
            f"{self._base_url}/chat/completions",
            json=payload,
            timeout=request_timeout,
        )
        wire.request = request
        started = time.monotonic()
        turn = StreamedTurn()
        streamed = wire.streamed if wire.streamed is not None else []
        try:
            response = self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            log.error("llm_request_failed", error=str(exc))
            raise request_failed(exc) from exc
        wire.response = response
        try:
            if response.status_code != 200:
                body = response.read().decode("utf-8", errors="replace")
                streamed.append(body)
                log.error("llm_request_failed", status=response.status_code)
                raise bad_status(response.status_code, body)
            for line in response.iter_lines():
                streamed.append(line)
                turn.feed(line)
                if turn.done:
                    break
                if turn.first == "content":
                    # The model has begun its closing line: stop it here.
                    log.info("llm_peek_words", duration_ms=_ms(started))
                    return None
        except httpx.HTTPError as exc:
            log.error("llm_request_failed", error=str(exc))
            raise request_failed(exc) from exc
        finally:
            response.close()
        if turn.first is None:
            log.info("llm_peek_empty", duration_ms=_ms(started))
            return None
        completion = turn.completion()
        choice = completion.choices[0]
        log.info(
            "llm_response",
            duration_ms=_ms(started),
            streamed=True,
            finish_reason=choice.finish_reason,
            tool_calls=len(choice.message.tool_calls),
        )
        return chat_result(completion)

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

        def parse(completion: WireCompletion) -> TStructured:
            result = chat_result(completion)
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
        temperature: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            # The profile's sampling: only the keys it sets, so an absent one
            # is the server's default. llama-server accepts ``top_k`` and
            # ``chat_template_kwargs`` as plain body fields (no SDK
            # extra_body indirection needed).
            **self._profile.sampling,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        kwarg = self._profile.thinking_kwarg
        if kwarg is not None:
            payload["chat_template_kwargs"] = {kwarg: enable_thinking}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if self._seed is not None:
            payload["seed"] = self._seed
        if tools:
            payload["tools"] = [tool.to_wire() for tool in tools]
            payload["tool_choice"] = "auto"
        return payload

    def _post(
        self,
        payload: dict[str, Any],
        parse: Callable[[WireCompletion], TParsed],
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
    ) -> WireCompletion:
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
            raise request_failed(exc) from exc
        if wire is not None:
            wire.response = response
        duration_ms = round((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            log.error("llm_request_failed", status=response.status_code, duration_ms=duration_ms)
            raise bad_status(response.status_code, response.text)
        try:
            completion = parse_completion(response.text)
        except ProviderResponseError as exc:
            log.error("llm_response_invalid", duration_ms=duration_ms, error=str(exc))
            raise
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
            logger.warning("context_capture_failed", error=str(exc), error_type=type(exc).__name__)
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
            if wire.streamed is not None:
                # A streamed call (#516): the lines read, not the whole body,
                # which a stream stopped early never had.
                body = None if response is None else "\n".join(wire.streamed)
            else:
                body = None if response is None else body_text(response.content)
            self._capture.record(
                stamp,
                {
                    "url": None if request is None else str(request.url),
                    "ms": round((time.monotonic() - started) * 1000),
                    "request": None if request is None else body_text(request.content),
                    "status_code": None if response is None else response.status_code,
                    "response": body,
                    "error": "" if error is None else f"{type(error).__name__}: {error}",
                    # A server that never answered cannot render either; asking
                    # would only stall a run that is already failing.
                    "template": None
                    if request is None or response is None
                    else self._render_template(request.content),
                },
            )
        except Exception as exc:
            logger.warning("context_capture_failed", error=str(exc), error_type=type(exc).__name__)

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


def provider_from_settings(*, seed: int | None = None) -> LlamaCppProvider:
    """The provider as configured (``LLAMACPP_*`` env / ``.env``), capturing
    every call's bytes when ``PCC_CONTEXT_PATH`` is set (#401). ``seed`` is
    the eval harness's paired-sample seed (#403); the app never passes one."""
    settings = get_settings()
    return LlamaCppProvider(
        settings.llamacpp_base_url,
        settings.llamacpp_model,
        timeout_seconds=settings.llamacpp_timeout_seconds,
        capture=context_capture_from_settings(),
        seed=seed,
    )


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
