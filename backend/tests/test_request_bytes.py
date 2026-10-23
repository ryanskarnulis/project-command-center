"""The request bytes of one canonical run, pinned byte for byte (#402).

A real :class:`LlamaCppProvider` over a faked llama-server drives two loop
runs through the shared registry and an empty in-memory DB:

1. a fresh ask: ``list_projects`` (answers ``[]``), ``get_task`` on an id that
   does not exist (a domain refusal), then the planner's note and the
   narrator's call (#418);
2. a follow-up with one exchange of history (its tool records included, so
   the recall carries a record line), capped at one iteration, so it stops
   at ``max_iterations`` after a lookup and the narrator speaks for the
   limit stop (#399).

Every body the provider built (``request.content``, since #401 literally what
goes on the wire) is compared with ``fixtures/canonical_run_requests.jsonl``,
one exact body per line. The run is deterministic: the date is fixed, the
tool-call ids are the fake server's, and nothing the tools answer carries a
timestamp or a generated id.

So a change to either phase's prompt, the personality, the recall, a tool
schema, the sampling, the token caps or the thinking kwarg shows up here as a
fixture diff — and a fixture diff is a model-facing change, which runs the
eval gate (``docs/agent-design.md``, "Pinned bytes"). Regenerate only for a
change that is *meant* to move the bytes, and say so in its PR::

    cd backend && PCC_UPDATE_FIXTURES=1 .venv/bin/pytest tests/test_request_bytes.py \
        tests/test_tool_schema_snapshot.py
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import loop as loop_module
from app.ai.loop import AgentLoop
from app.ai.model_profile import load_profile
from app.ai.providers.llamacpp import LlamaCppProvider
from app.tools import runtime

FIXTURE = Path(__file__).parent / "fixtures" / "canonical_run_requests.jsonl"

# The model the canonical run names: the shipped default.
_MODEL = "gemma-4-12b"
_TODAY = date(2026, 7, 15)


class _FixedDate(date):
    """``date`` with a fixed ``today()``, so the state block's calendar is stable."""

    @classmethod
    def today(cls) -> _FixedDate:
        return cls(_TODAY.year, _TODAY.month, _TODAY.day)


def _completion(message: dict[str, Any], finish: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        },
    )


def _tool_call(index: int, name: str, arguments: dict[str, Any]) -> httpx.Response:
    call = {
        "id": f"call_{index}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return _completion({"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls")


def _text(content: str) -> httpx.Response:
    return _completion({"role": "assistant", "content": content}, "stop")


class _Server:
    """Answers by what each request is: a planner turn (tools offered) takes
    the next step of the script by how many tool results its history already
    holds; a request without tools is the narrator's and gets words."""

    _SCRIPT: tuple[tuple[str, dict[str, Any]], ...] = (
        ("list_projects", {}),
        ("get_task", {"task_id": 999}),
    )

    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        self.bodies.append(request.content)
        body = json.loads(request.content)
        if "tools" not in body:
            return _text("I looked up your projects; there are none yet.")
        answered = sum(1 for message in body["messages"] if message["role"] == "tool")
        if answered < len(self._SCRIPT):
            name, arguments = self._SCRIPT[answered]
            return _tool_call(answered, name, arguments)
        return _text("You have no projects yet, and there is no task 999.")


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def canonical_run_bodies(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """The exact request bodies of the canonical run, in the order sent."""
    monkeypatch.setattr(loop_module, "date", _FixedDate)
    server = _Server()
    client = httpx.Client(transport=httpx.MockTransport(server))
    provider = LlamaCppProvider("http://llm.test/v1", _MODEL, client=client)
    first = AgentLoop(provider, tracer=_NoTrace()).run(
        "What projects do I have, and what is task 999?"
    )
    assert first.stop_reason == "completed"
    assert first.reply == "I looked up your projects; there are none yet."
    assert first.note == "You have no projects yet, and there is no task 999."
    second = AgentLoop(provider, max_iterations=1, tracer=_NoTrace()).run(
        "Check my projects again.",
        history=[
            {"role": "user", "content": "What projects do I have, and what is task 999?"},
            {
                "role": "assistant",
                "content": first.reply,
                "tool_calls": [record.model_dump() for record in first.tool_calls],
            },
        ],
    )
    assert second.stop_reason == "max_iterations"
    assert second.reply == "I looked up your projects; there are none yet."
    return server.bodies


class _NoTrace:
    def write(self, record: dict[str, Any]) -> None:
        pass


def _serialized(bodies: list[bytes]) -> bytes:
    # A compact JSON body never holds a raw newline, so one body per line is
    # exact and a diff names the request that moved.
    for body in bodies:
        assert b"\n" not in body
    return b"".join(body + b"\n" for body in bodies)


def test_the_canonical_run_sends_the_pinned_bytes(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _serialized(canonical_run_bodies(monkeypatch))
    if os.environ.get("PCC_UPDATE_FIXTURES") == "1":
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_bytes(sent)
    pinned = FIXTURE.read_bytes()
    now, then = sent.splitlines(), pinned.splitlines()
    assert len(now) == len(then), f"{len(now)} requests sent, {len(then)} pinned"
    for index, (got, want) in enumerate(zip(now, then, strict=True)):
        assert got == want, (
            f"request {index} of the canonical run changed — a model-facing change: "
            "regenerate with PCC_UPDATE_FIXTURES=1, read the fixture diff, and run "
            "the eval gate (docs/agent-design.md, 'Pinned bytes')"
        )


def test_the_canonical_run_covers_both_phases_tools_and_recall() -> None:
    # What the pin is worth depends on what it drove: a fixture that never
    # reached a phase would pass any change to it.
    bodies = [json.loads(line) for line in FIXTURE.read_bytes().splitlines()]
    turns = [body for body in bodies if "tools" in body]
    narrations = [body for body in bodies if "tools" not in body]
    assert len(turns) == 4 and len(narrations) == 2
    assert {body["model"] for body in bodies} == {_MODEL}
    assert all(body["tool_choice"] == "auto" for body in turns)
    # Every request carries the model profile's settings (#402).
    profile = load_profile(_MODEL)
    # The planner's calls at its own temperature (#418), the narrator's at
    # the sampling's.
    planner_sampling = {**profile.sampling.wire(), "temperature": profile.turn_temperature}
    for body in bodies:
        expected = planner_sampling if "tools" in body else profile.sampling.wire()
        assert {key: body[key] for key in expected} == expected
        assert body["chat_template_kwargs"] == {profile.thinking_kwarg: False}
    assert {body["max_tokens"] for body in turns} == {profile.turn_max_tokens}
    assert {body["max_tokens"] for body in narrations} == {profile.narrator_max_tokens}
    assert {body["messages"][0]["content"] for body in narrations} == {loop_module.NARRATOR_PROMPT}
    assert {message["role"] for message in turns[2]["messages"]} == {
        "system",
        "user",
        "assistant",
        "tool",
    }
    # The follow-up carries its history as recall inside its one user turn.
    assert [message["role"] for message in turns[3]["messages"]] == ["system", "user"]
    follow_up = turns[3]["messages"][1]["content"]
    assert 'The user said: "What projects do I have, and what is task 999?"' in follow_up
    # The first run's read is remembered by what it found; its failed read not.
    assert 'looked up then: list_projects {} -> found nothing' in follow_up
    assert "get_task" not in follow_up
    assert follow_up.endswith("Check my projects again.")
    # The second narrator brief is a limit stop's, with the recall at its head.
    assert narrations[1]["messages"][1]["content"].startswith("Before this run:")
    assert "What you said then (your words, not a record)" in narrations[1]["messages"][1]["content"]
    # The date is the state block's, at the head of the current turn (#412);
    # the system prompt carries none, so its bytes never move with the day.
    assert _TODAY.isoformat() not in turns[0]["messages"][0]["content"]
    assert f"Today: Wednesday {_TODAY.isoformat()}" in turns[0]["messages"][-1]["content"]
    # The refusal reached the model as a tool result, not a crash.
    assert "999" in turns[2]["messages"][-1]["content"]
