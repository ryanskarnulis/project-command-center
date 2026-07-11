"""Opt-in integration tests against the live shared runtime (llama-swap :8200).

Skipped unless ``PCC_LLM_INTEGRATION=1``, so CI and default local runs never
touch the GPU. This is the slice's documented smoke — run it to prove the
vertical path until the agent loop exists:

    cd backend
    PCC_LLM_INTEGRATION=1 .venv/bin/pytest tests/test_ai_llamacpp_integration.py -v

``LLAMACPP_BASE_URL`` / ``LLAMACPP_MODEL`` override the defaults (host
loopback :8200, gemma-4-12b). The first call may cold-load the model —
~100 s worst case; the provider's timeout already tolerates it.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from app.ai.providers.llamacpp import ToolSpec, provider_from_settings, tool_result_message

pytestmark = pytest.mark.skipif(
    os.environ.get("PCC_LLM_INTEGRATION") != "1",
    reason="live-runtime integration is opt-in: set PCC_LLM_INTEGRATION=1",
)

_CREATE_TASK = ToolSpec(
    name="create_task",
    description="Create a task in the user's task manager.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short task title."},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["title", "priority"],
    },
)


def test_tool_call_round_trip() -> None:
    messages = [
        {
            "role": "system",
            "content": "You manage the user's tasks. Use the provided tools for any task request.",
        },
        {"role": "user", "content": 'Create a high-priority task titled "Water the plants".'},
    ]
    with provider_from_settings() as provider:
        first = provider.chat(messages, tools=[_CREATE_TASK])

        assert len(first.tool_calls) == 1
        call = first.tool_calls[0]
        assert call.name == "create_task"
        assert call.arguments["priority"] == "high"
        assert "water" in call.arguments["title"].lower()

        second = provider.chat(
            [
                *messages,
                first.to_message(),
                tool_result_message(call.id, '{"task_id": 42, "status": "created"}'),
            ],
            tools=[_CREATE_TASK],
        )
        # The follow-up turn must produce user-facing text, not another call.
        assert second.tool_calls == []
        assert second.content


class _TaskDraft(BaseModel):
    # No defaults: required model-output fields must always be emitted.
    title: str
    priority: str


def test_structured_output_round_trip() -> None:
    with provider_from_settings() as provider:
        draft = provider.chat_structured(
            [
                {"role": "system", "content": "Extract the task the user describes."},
                {"role": "user", "content": "Remind me to water the plants; it's urgent."},
            ],
            schema=_TaskDraft,
        )
    # The grammar guarantees both fields exist; their wording is the model's.
    assert draft.title
    assert draft.priority
