"""Unit tests for the llama.cpp provider against faked wire responses.

``httpx.MockTransport`` plays the server: each test hands the provider a
client whose transport returns a canned ``/chat/completions`` body (or
raises), so the full request-build -> wire-validate -> result path runs
without a model. The opt-in live test is ``test_ai_llamacpp_integration.py``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.ai.providers.llamacpp import (
    ChatResult,
    LlamaCppProvider,
    ProviderRequestError,
    ProviderResponseError,
    ToolCall,
    ToolCallArgumentsError,
    ToolSpec,
    tool_result_message,
)

_USER = [{"role": "user", "content": "hello"}]

_CREATE_TASK = ToolSpec(
    name="create_task",
    description="Create a task.",
    parameters={
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    },
)


def _completion_body(message: dict[str, Any], finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "gemma-4-12b",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _provider_returning(
    body: dict[str, Any], *, status_code: int = 200, captured: list[dict[str, Any]] | None = None
) -> LlamaCppProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(json.loads(request.content))
        return httpx.Response(status_code, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LlamaCppProvider("http://llm.test/v1", "gemma-4-12b", client=client)


def test_chat_returns_text_and_sets_sampling_per_request() -> None:
    captured: list[dict[str, Any]] = []
    body = _completion_body({"role": "assistant", "content": "hi there"})
    result = _provider_returning(body, captured=captured).chat(_USER)

    assert result.content == "hi there"
    assert result.tool_calls == []
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.total_tokens == 15

    payload = captured[0]
    assert payload["model"] == "gemma-4-12b"
    # Sampling is always set per request (server flags are defaults only) and
    # thinking defaults off for fast tool calls.
    assert (payload["temperature"], payload["top_p"], payload["top_k"]) == (1.0, 0.95, 64)
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "tools" not in payload and "response_format" not in payload


def test_chat_with_tools_parses_tool_call_arguments() -> None:
    captured: list[dict[str, Any]] = []
    body = _completion_body(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "create_task",
                        "arguments": '{"title": "Water the plants"}',
                    },
                }
            ],
        },
        finish_reason="tool_calls",
    )
    result = _provider_returning(body, captured=captured).chat(_USER, tools=[_CREATE_TASK])

    assert result.content is None
    call = result.tool_calls[0]
    assert (call.id, call.name) == ("call-1", "create_task")
    assert call.arguments == {"title": "Water the plants"}

    payload = captured[0]
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["function"]["name"] == "create_task"
    assert payload["tools"][0]["function"]["parameters"] == _CREATE_TASK.parameters


def test_empty_tool_arguments_become_empty_dict() -> None:
    body = _completion_body(
        {
            "role": "assistant",
            "tool_calls": [{"id": "c", "function": {"name": "create_task", "arguments": ""}}],
        }
    )
    result = _provider_returning(body).chat(_USER, tools=[_CREATE_TASK])
    assert result.tool_calls[0].arguments == {}


def test_malformed_tool_arguments_raise_not_best_effort() -> None:
    body = _completion_body(
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c", "function": {"name": "create_task", "arguments": '{"title": '}}
            ],
        }
    )
    with pytest.raises(ToolCallArgumentsError) as excinfo:
        _provider_returning(body).chat(_USER, tools=[_CREATE_TASK])
    assert excinfo.value.tool_name == "create_task"


def test_non_object_tool_arguments_raise() -> None:
    body = _completion_body(
        {
            "role": "assistant",
            "tool_calls": [{"id": "c", "function": {"name": "create_task", "arguments": "[1]"}}],
        }
    )
    with pytest.raises(ToolCallArgumentsError):
        _provider_returning(body).chat(_USER, tools=[_CREATE_TASK])


def test_reasoning_content_is_never_answer_text_and_never_round_trips() -> None:
    body = _completion_body(
        {
            "role": "assistant",
            "content": "final answer",
            "reasoning_content": "secret chain of thought",
        }
    )
    result = _provider_returning(body).chat(_USER)
    assert result.content == "final answer"
    assert "reasoning_content" not in result.to_message()


def test_to_message_re_serializes_tool_calls_for_history() -> None:
    result = ChatResult(
        content=None,
        tool_calls=[ToolCall(id="call-1", name="create_task", arguments={"title": "x"})],
        finish_reason="tool_calls",
        usage=None,
    )
    message = result.to_message()
    assert message["role"] == "assistant"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"title": "x"}'
    assert tool_result_message("call-1", "ok") == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "ok",
    }


class _TaskDraft(BaseModel):
    # No defaults: required model-output fields must always be emitted, or
    # json_schema omission silently yields None (see project memory).
    title: str
    priority: str


def test_chat_structured_sends_schema_and_validates() -> None:
    captured: list[dict[str, Any]] = []
    body = _completion_body(
        {"role": "assistant", "content": '{"title": "Water plants", "priority": "high"}'}
    )
    draft = _provider_returning(body, captured=captured).chat_structured(
        _USER, schema=_TaskDraft
    )
    assert draft == _TaskDraft(title="Water plants", priority="high")

    response_format = captured[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == _TaskDraft.model_json_schema()
    # Grammar constraint and a thinking preamble are mutually exclusive.
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_chat_structured_rejects_schema_violations() -> None:
    body = _completion_body({"role": "assistant", "content": '{"title": "no priority"}'})
    with pytest.raises(ProviderResponseError, match="_TaskDraft validation"):
        _provider_returning(body).chat_structured(_USER, schema=_TaskDraft)


def test_http_error_status_raises_request_error() -> None:
    with pytest.raises(ProviderRequestError, match="500"):
        _provider_returning({"error": "boom"}, status_code=500).chat(_USER)


def test_network_failure_raises_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = LlamaCppProvider("http://llm.test/v1", "gemma-4-12b", client=client)
    with pytest.raises(ProviderRequestError, match="request failed"):
        provider.chat(_USER)


def test_invalid_wire_body_raises_response_error() -> None:
    with pytest.raises(ProviderResponseError, match="wire validation"):
        _provider_returning({"object": "chat.completion", "choices": []}).chat(_USER)
