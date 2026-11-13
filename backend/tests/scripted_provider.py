"""A scripted stand-in for the llama.cpp provider, shared by the agent tests.

Plays the model without a GPU: each ``chat()`` call pops the next scripted
turn (a ``ChatResult``, or an exception to raise) and records the request, so
loop behavior can be asserted deterministically end-to-end.

The planner and the narrator (#418) are scripted apart: a call offered tools
is the planner's and pops ``turns``; a tool-free call is the narrator's and
pops ``narrations``. A narrator call with nothing scripted fails like a dead
server would, so the loop falls back to the planner's note — which is what a
test about the planner alone wants its reply to be.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.ai.providers.llamacpp import ChatResult, ProviderRequestError, ToolCall, ToolSpec


def text_turn(content: str) -> ChatResult:
    return ChatResult(content=content, tool_calls=[], finish_reason="stop", usage=None)


def tool_calls_turn(*calls: tuple[str, dict[str, Any]]) -> ChatResult:
    return ChatResult(
        content=None,
        tool_calls=[
            ToolCall(id=f"call_{index}", name=name, arguments=arguments)
            for index, (name, arguments) in enumerate(calls)
        ],
        finish_reason="tool_calls",
        usage=None,
    )


class ScriptedProvider:
    """Pops one scripted turn per ``chat()`` call; records every request."""

    def __init__(
        self,
        turns: Sequence[ChatResult | Exception],
        narrations: Sequence[ChatResult | Exception] = (),
    ) -> None:
        self._turns = list(turns)
        self._narrations = list(narrations)
        self.requests: list[dict[str, Any]] = []

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
        self.requests.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        if tools is None:
            if not self._narrations:
                raise ProviderRequestError("scripted: no narration scripted")
            turn = self._narrations.pop(0)
        else:
            assert self._turns, "provider called more times than scripted"
            turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


class PeekingProvider(ScriptedProvider):
    """A scripted provider that can also ``peek`` (#516), as the real one does.

    A peek pops the next planner turn like ``chat`` and answers as
    ``LlamaCppProvider.peek`` would: ``None`` for a turn that opens with words
    (the stream stopped at them), the turn itself for one that calls tools.
    ``sent`` names each planner or narrator call in order: ``chat``, ``peek``
    or ``narrate``.
    """

    def __init__(
        self,
        turns: Sequence[ChatResult | Exception],
        narrations: Sequence[ChatResult | Exception] = (),
    ) -> None:
        super().__init__(turns, narrations)
        self.sent: list[str] = []

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
        self.sent.append("chat" if tools is not None else "narrate")
        return super().chat(
            messages,
            tools=tools,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens,
            timeout=timeout,
            temperature=temperature,
        )

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
        turn = super().chat(
            messages,
            tools=tools,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens,
            timeout=timeout,
            temperature=temperature,
        )
        self.sent.append("peek")
        return turn if turn.tool_calls else None
