"""Bounded model-facing conversation history (#244).

A per-turn cap does not bound a conversation: 200 individually valid
8,000-character turns is 1.6M characters in one request, which either
overflows the runtime's 131,072-token window or spends the whole run budget
prefilling it. These cover the windowing policy
(``app/ai/context_budget.py``), the service layer that applies it, the loop's
last-gate re-application, and the bounded conversation-detail contract that
keeps the *persisted* transcript reachable regardless.
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai import context_budget
from app.ai.loop import AgentLoop, AgentRunResult, build_system_prompt
from app.api import routes_agent
from app.schemas.conversations import MAX_AGENT_MESSAGE_LENGTH
from app.services import conversations as conversations_service
from app.tools import registry
from tests.scripted_provider import ScriptedProvider, text_turn

# The issue's reproduction: 100 user + 100 assistant turns, each at the
# documented per-message maximum.
_TURNS = 100
_MAX_TURN = "x" * MAX_AGENT_MESSAGE_LENGTH


def _seed_huge_conversation(db: Session) -> int:
    conversation = conversations_service.create_conversation(db)
    for index in range(_TURNS):
        conversations_service.append_user_message(
            db, conversation, f"{index:04d} " + _MAX_TURN[:-5]
        )
        conversations_service.append_assistant_message(
            db,
            conversation,
            AgentRunResult(
                reply=f"{index:04d} " + _MAX_TURN[:-5],
                stop_reason="completed",
                tool_calls=[],
                iterations=1,
                messages=[],
            ),
        )
    return int(conversation.id)


# --- Policy -------------------------------------------------------------------


def test_reserves_leave_a_positive_budget_and_fit_the_real_prompt() -> None:
    """The static reserve really does cover the prompt the loop sends."""
    overhead = context_budget.estimate_tokens(
        build_system_prompt(date.today())
    ) + sum(
        context_budget.estimate_tokens(spec.model_dump_json())
        for spec in registry.tool_specs()
    )
    assert overhead <= context_budget.STATIC_PROMPT_RESERVE_TOKENS

    budget = context_budget.history_token_budget()
    assert budget > 0
    # History plus every reserve stays inside the configured window.
    assert (
        budget
        + context_budget.DEFAULT_PROMPT_OVERHEAD_TOKENS
        + context_budget.TOOL_TRANSCRIPT_RESERVE_TOKENS
        + context_budget.COMPLETION_RESERVE_TOKENS
        <= context_budget.MODEL_CONTEXT_TOKENS
    )


def test_pathological_overhead_yields_no_history_not_a_negative_budget() -> None:
    assert (
        context_budget.history_token_budget(
            prompt_overhead_tokens=context_budget.MODEL_CONTEXT_TOKENS * 2
        )
        == 0
    )


def test_fit_history_keeps_the_newest_coherent_window() -> None:
    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]
    # Room for roughly two of these tiny turns.
    budget = 2 * (context_budget.estimate_tokens("three") + context_budget.MESSAGE_FRAMING_TOKENS)
    kept = context_budget.fit_history(history, budget_tokens=budget)
    assert kept == history[-2:]
    assert kept[0]["role"] == "user"


def test_fit_history_never_strands_an_assistant_turn() -> None:
    """A window that would start on an assistant reply is trimmed to its user turn."""
    history = [
        {"role": "user", "content": "the question"},
        {"role": "assistant", "content": "the answer"},
    ]
    # Enough for the assistant turn alone, not for the user turn before it.
    budget = context_budget.estimate_tokens("the answer") + context_budget.MESSAGE_FRAMING_TOKENS
    assert context_budget.fit_history(history, budget_tokens=budget) == []


def test_fit_history_is_deterministic_and_idempotent() -> None:
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "z" * 300}
        for index in range(50)
    ]
    once = context_budget.fit_history(history, budget_tokens=400)
    assert once == context_budget.fit_history(history, budget_tokens=400)
    assert context_budget.fit_history(once, budget_tokens=400) == once


def test_fit_history_does_not_skip_a_turn_to_squeeze_an_older_one_in() -> None:
    """Stopping at the first turn that doesn't fit keeps the window contiguous."""
    history = [
        {"role": "user", "content": "tiny"},
        {"role": "user", "content": "q" * 3_000},
        {"role": "user", "content": "also tiny"},
    ]
    kept = context_budget.fit_history(history, budget_tokens=50)
    assert kept == [{"role": "user", "content": "also tiny"}]


# --- Service layer ------------------------------------------------------------


def test_history_for_loop_is_bounded_for_a_huge_conversation(
    db_session: Session,
) -> None:
    conversation_id = _seed_huge_conversation(db_session)

    # The persisted transcript is untouched — this is a windowing change on the
    # way out to the model, not a change to what is stored.
    assert len(conversations_service.list_messages(db_session, conversation_id)) == (
        _TURNS * 2
    )

    history = conversations_service.history_for_loop(db_session, conversation_id)
    budget = context_budget.history_token_budget()
    cost = sum(
        context_budget.estimate_tokens(m["content"]) + context_budget.MESSAGE_FRAMING_TOKENS
        for m in history
    )
    assert 0 < cost <= budget
    assert len(history) < _TURNS * 2
    # The newest turns are the ones kept, and the window starts on a user turn.
    assert history[0]["role"] == "user"
    assert history[-1]["content"].startswith(f"{_TURNS - 1:04d} ")


def test_history_for_loop_keeps_a_short_conversation_whole(
    db_session: Session,
) -> None:
    conversation = conversations_service.create_conversation(db_session)
    conversations_service.append_user_message(db_session, conversation, "hello")
    conversations_service.append_assistant_message(
        db_session,
        conversation,
        AgentRunResult(
            reply="hi", stop_reason="completed", tool_calls=[], iterations=1, messages=[]
        ),
    )
    assert conversations_service.history_for_loop(db_session, conversation.id) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


# --- Loop ---------------------------------------------------------------------


def test_loop_windows_history_before_the_provider_request() -> None:
    """An over-budget history handed straight to the loop is trimmed, not sent."""
    provider = ScriptedProvider([text_turn("done")])
    loop = AgentLoop(provider)
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": _MAX_TURN}
        for index in range(200)
    ]

    loop.run("what now?", history=history)

    sent = provider.requests[0]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[-1] == {"role": "user", "content": "what now?"}
    # The system prompt and the current turn always survive; history does not.
    assert len(sent) < len(history) + 2
    total = sum(context_budget.estimate_tokens(str(m["content"])) for m in sent)
    assert total < context_budget.MODEL_CONTEXT_TOKENS


def test_loop_leaves_an_in_budget_history_alone() -> None:
    provider = ScriptedProvider([text_turn("done")])
    history = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "answered"},
    ]
    AgentLoop(provider).run("follow up", history=history)
    assert provider.requests[0]["messages"][1:-1] == history


# --- Bounded conversation detail ----------------------------------------------


def test_conversation_detail_is_paginated(client: TestClient, db_session: Session) -> None:
    conversation = conversations_service.create_conversation(db_session)
    for index in range(10):
        conversations_service.append_user_message(db_session, conversation, f"turn {index}")
    db_session.commit()

    newest = client.get(
        f"/api/agent/conversations/{conversation.id}", params={"limit": 4}
    )
    assert newest.status_code == 200
    body = newest.json()
    assert body["message_count"] == 10
    assert body["has_more"] is True
    assert [m["content"] for m in body["messages"]] == [
        "turn 6",
        "turn 7",
        "turn 8",
        "turn 9",
    ]

    older = client.get(
        f"/api/agent/conversations/{conversation.id}",
        params={"limit": 4, "before_id": body["messages"][0]["id"]},
    )
    older_body = older.json()
    assert [m["content"] for m in older_body["messages"]] == [
        "turn 2",
        "turn 3",
        "turn 4",
        "turn 5",
    ]
    assert older_body["has_more"] is True

    oldest = client.get(
        f"/api/agent/conversations/{conversation.id}",
        params={"limit": 4, "before_id": older_body["messages"][0]["id"]},
    )
    oldest_body = oldest.json()
    assert [m["content"] for m in oldest_body["messages"]] == ["turn 0", "turn 1"]
    assert oldest_body["has_more"] is False


def test_conversation_detail_rejects_an_unbounded_page(client: TestClient) -> None:
    created = client.post("/api/agent/conversations", json={})
    conversation_id = created.json()["id"]
    over = client.get(
        f"/api/agent/conversations/{conversation_id}",
        params={"limit": routes_agent.MAX_MESSAGE_LIMIT + 1},
    )
    assert over.status_code == 422
