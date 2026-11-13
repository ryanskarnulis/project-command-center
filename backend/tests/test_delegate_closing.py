"""Agent callers: the trace joins (#517) and the code-built closing note (#516).

The contract is ``../agent-standard/delegate-api.md`` "Agent callers and the
closing note (v1.1)". Conductor sends ``X-Agent-Turn`` and ``X-Agent-Hop`` on
every delegate call; the run's trace rows carry both. After a delegate's turn
whose calls all landed as writes, the next planner call is peeked: stopped at
its first words, the reply then a note code builds from the record; read whole
when it opens with a tool call. Everything else keeps today's path: the human
caller's closing turn and narrator, a refusal, a read, a question, a
confirmation question, a limit, a Stop, a provider with no peek.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import closing, confirmation
from app.ai import trace as run_trace
from app.ai.context_capture import JsonlContextCapture
from app.ai.loop import AgentLoop, AgentRunFailed
from app.ai.providers.llamacpp import ProviderRequestError
from app.api import routes_agent
from app.main import app
from app.tools import runtime
from tests.claims import Evidence, ToolCall, read_claims, unbacked
from tests.scripted_provider import (
    PeekingProvider,
    ScriptedProvider,
    text_turn,
    tool_calls_turn,
)

DELEGATE = {"X-Agent-Actor": "agent:conductor"}
TURN = "4f1c2a9b8e7d6c5b4a3f2e1d0c9b8a7f"
HOP = "a1b2c3d4e5f60718"


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    confirmation.reset()
    yield factory
    confirmation.reset()


@pytest.fixture
def trace_path(tmp_path: Path) -> Path:
    return tmp_path / "agent.jsonl"


def _use(provider: ScriptedProvider, trace_path: Path, **options: Any) -> None:
    app.dependency_overrides[routes_agent.get_agent_loop] = lambda: AgentLoop(
        provider, tracer=run_trace.JsonlTracer(trace_path), **options
    )


def _thread(client: TestClient) -> int:
    return int(client.post("/api/agent/conversations", json={}).json()["id"])


def _post(
    client: TestClient,
    conversation_id: int,
    content: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    response: httpx.Response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=headers or {},
    )
    return response


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _only(path: Path) -> dict[str, Any]:
    [record] = _records(path)
    return record


def _ask(
    client: TestClient,
    provider: ScriptedProvider,
    trace_path: Path,
    content: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    _use(provider, trace_path)
    response = _post(client, _thread(client), content, headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()["assistant_message"]
    return body


def _kitchen(client: TestClient) -> int:
    project_id = int(client.post("/api/projects", json={"name": "Kitchen"}).json()["id"])
    for title in ("Wipe the counters", "Clean the oven", "Empty the pantry"):
        response = client.post("/api/tasks", json={"title": title, "project_id": project_id})
        assert response.status_code == 201, response.text
    return project_id


TOMORROW = (date.today() + timedelta(days=1)).isoformat()
WATER = ("create_task", {"title": "Water the plants", "due_date": TOMORROW})


# --- #517: the trace joins conductor's turn and hop ---------------------------------


def test_a_delegated_run_records_conductors_turn_and_hop(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = ScriptedProvider([text_turn("Nothing to do.")])
    _ask(
        client,
        provider,
        trace_path,
        "hello",
        {**DELEGATE, "X-Agent-Turn": TURN, "X-Agent-Hop": HOP},
    )
    record = _only(trace_path)
    assert record["schema"] == run_trace.TRACE_SCHEMA == 6
    assert (record["agent_turn"], record["agent_hop"]) == (TURN, HOP)


def test_a_run_without_the_headers_records_none(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    _ask(client, ScriptedProvider([text_turn("Hi.")]), trace_path, "hello")
    record = _only(trace_path)
    assert (record["agent_turn"], record["agent_hop"]) == (None, None)


@pytest.mark.parametrize("bad", ["has spaces", "x" * 129, "semi;colon", ""])
def test_a_malformed_trace_id_is_dropped_never_refused(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path, bad: str
) -> None:
    """Diagnostics never cost a request: a value that isn't an id is left out."""
    _ask(
        client,
        ScriptedProvider([text_turn("Hi.")]),
        trace_path,
        "hello",
        {**DELEGATE, "X-Agent-Turn": bad, "X-Agent-Hop": HOP},
    )
    record = _only(trace_path)
    assert (record["agent_turn"], record["agent_hop"]) == (None, HOP)


def test_a_capture_line_carries_the_turn_and_hop(tmp_path: Path) -> None:
    capture = JsonlContextCapture(tmp_path / "context.jsonl")
    with structlog.contextvars.bound_contextvars(run_id="r1", agent_turn=TURN, agent_hop=HOP):
        stamp = capture.begin()
    capture.record(stamp, {"request": "{}"})
    [line] = [json.loads(text) for text in (tmp_path / "context.jsonl").read_text().splitlines()]
    assert line["schema"] == 2
    assert (line["run_id"], line["agent_turn"], line["agent_hop"]) == ("r1", TURN, HOP)


def test_the_tool_turn_and_the_closing_turn_are_labelled_apart(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = ScriptedProvider(
        [tool_calls_turn(WATER), text_turn("Made it.")],
        narrations=[text_turn("Added Water the plants for tomorrow.")],
    )
    _ask(client, provider, trace_path, "add a task to water the plants tomorrow")
    phases = [call["phase"] for call in _only(trace_path)["calls"]]
    assert phases == ["planner", "closing", "narrator"]


# --- #516: the closing turn is peeked, and stopped at its first words ---------------


def test_a_delegated_write_stops_the_closing_turn_at_its_first_words(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = PeekingProvider([tool_calls_turn(WATER), text_turn("Made it.")])
    body = _ask(client, provider, trace_path, "add a task to water the plants tomorrow", DELEGATE)

    assert provider.sent == ["chat", "peek"]
    assert body["stop_reason"] == "completed"
    [entry] = body["record"]
    task_id = entry["touched"][0]["id"]
    assert entry["outcome"] == "done"
    assert body["content"] == f'Created task {task_id} "Water the plants", due {TOMORROW}.'
    record = _only(trace_path)
    assert record["closed_by_code"] is True and record["handed_off"] is True
    assert record["note"] is None and record["reply"] == body["content"]
    assert [(c["phase"], c["status"]) for c in record["calls"]] == [
        ("planner", "ok"),
        ("closing", "peeked"),
    ]
    assert isinstance(record["peek_ms"], int)


def test_a_human_caller_is_never_peeked(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = PeekingProvider(
        [tool_calls_turn(WATER), text_turn("Made it.")],
        narrations=[text_turn("Added Water the plants for tomorrow.")],
    )
    body = _ask(client, provider, trace_path, "add a task to water the plants tomorrow")

    assert provider.sent == ["chat", "chat", "narrate"]
    assert body["content"] == "Added Water the plants for tomorrow."
    record = _only(trace_path)
    assert record["closed_by_code"] is False and record["peek_ms"] is None


def test_an_unrecognised_actor_is_a_human_caller(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = PeekingProvider(
        [tool_calls_turn(WATER), text_turn("Made it.")],
        narrations=[text_turn("Added it.")],
    )
    _ask(
        client,
        provider,
        trace_path,
        "add a task to water the plants tomorrow",
        {"X-Agent-Actor": "agent:somebody"},
    )
    assert provider.sent == ["chat", "chat", "narrate"]


def test_a_peek_that_opens_with_a_tool_call_goes_on(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    """The planner writes one call per turn when the next needs the last
    one's id: the project, then its tasks. Each peek that opens with a tool
    call is read whole and run, so nothing is cut off; the note names every
    write."""
    provider = PeekingProvider(
        [
            tool_calls_turn(("create_project", {"name": "Garden"})),
            tool_calls_turn(("create_task", {"title": "Buy seeds", "project_id": 1})),
            tool_calls_turn(("create_task", {"title": "Dig beds", "project_id": 1})),
            text_turn("Made the project and both tasks."),
        ]
    )
    body = _ask(
        client,
        provider,
        trace_path,
        "create a Garden project with tasks Buy seeds and Dig beds",
        DELEGATE,
    )

    assert provider.sent == ["chat", "peek", "peek", "peek"]
    assert [entry["outcome"] for entry in body["record"]] == ["done", "done", "done"]
    ids = [entry["touched"][0]["id"] for entry in body["record"]]
    assert body["content"] == (
        f'Created project {ids[0]} "Garden". Created task {ids[1]} "Buy seeds". '
        f'Created task {ids[2]} "Dig beds".'
    )
    statuses = [c["status"] for c in _only(trace_path)["calls"]]
    assert statuses == ["ok", "ok", "ok", "peeked"]


def test_a_provider_without_peek_makes_the_plain_closing_call(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = ScriptedProvider([tool_calls_turn(WATER), text_turn("Made it.")])
    body = _ask(client, provider, trace_path, "add a task to water the plants tomorrow", DELEGATE)

    assert len(provider.requests) == 2
    assert body["content"].startswith("PCC's record of this run")
    assert _only(trace_path)["closed_by_code"] is False


def test_a_refused_write_does_not_arm_the_peek(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = PeekingProvider(
        [tool_calls_turn(("complete_task", {"task_id": 999})), text_turn("No task 999.")]
    )
    body = _ask(client, provider, trace_path, "mark task 999 done", DELEGATE)

    assert provider.sent == ["chat", "chat"]
    assert body["record"][0]["outcome"] == "refused"
    assert body["content"].startswith("PCC's record of this run")


def test_a_read_does_not_arm_the_peek(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    """A read's next words may answer a question: they stay the model's."""
    provider = PeekingProvider(
        [tool_calls_turn(("list_tasks", {})), text_turn("Nothing is due.")]
    )
    body = _ask(client, provider, trace_path, "what's due today", DELEGATE)

    assert provider.sent == ["chat", "chat"]
    assert body["record"][0]["outcome"] == "looked_up"
    assert "Nothing is due." in body["content"]


def test_a_question_to_the_person_stays_the_models_question(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    """A run that did nothing and asks (no tool call, or ``ask_user``) never
    reaches the peek, so its question still ends in "?" (conductor#187)."""
    provider = PeekingProvider([text_turn("Which project should it go in?")])
    body = _ask(client, provider, trace_path, "add it somewhere", DELEGATE)

    assert provider.sent == ["chat"]
    assert body["content"].rstrip().endswith('?"')
    assert body["record"] == []


def test_a_write_on_the_last_turn_keeps_its_limit_stop(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    """A run out of turns says so, as it would with the note."""
    provider = PeekingProvider([tool_calls_turn(WATER)])
    _use(provider, trace_path, max_iterations=1)
    response = _post(
        client, _thread(client), "add a task to water the plants tomorrow", DELEGATE
    )
    body = response.json()["assistant_message"]
    assert body["stop_reason"] == "max_iterations"
    assert provider.sent == ["chat"]
    assert _only(trace_path)["closed_by_code"] is False


def test_a_stop_during_the_write_keeps_its_cancelled_stop(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    cancel = threading.Event()

    class StopAfterTheTurn(PeekingProvider):
        def chat(self, *args: Any, **kwargs: Any) -> Any:
            turn = super().chat(*args, **kwargs)
            cancel.set()
            return turn

    provider = StopAfterTheTurn([tool_calls_turn(WATER), text_turn("Made it.")])
    loop = AgentLoop(provider, tracer=run_trace.JsonlTracer(trace_path))
    result = loop.run(
        "add a task to water the plants tomorrow", actor="agent:conductor", cancel=cancel
    )
    assert result.stop_reason == "cancelled"
    assert provider.sent == ["chat"]


def test_a_peek_that_fails_fails_like_the_call(
    tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    provider = PeekingProvider([tool_calls_turn(WATER), ProviderRequestError("went away")])
    loop = AgentLoop(provider, tracer=run_trace.JsonlTracer(trace_path))
    with pytest.raises(AgentRunFailed) as failed:
        loop.run("add a task to water the plants tomorrow", actor="agent:conductor")
    assert failed.value.result.stop_reason == "provider_error"
    assert [r.tool for r in failed.value.result.tool_calls] == ["create_task"]


def test_the_apps_own_yes_is_answered_by_code(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    """The v1 confirmation path: the question is unchanged, and the run that
    re-issues the call on "yes" ends on the code-built note."""
    project_id = _kitchen(client)
    trash = ("trash_project", {"project_id": project_id})
    _use(PeekingProvider([tool_calls_turn(trash)]), trace_path)
    conversation_id = _thread(client)
    asked = _post(client, conversation_id, "wipe the kitchen project", DELEGATE).json()
    assert asked["assistant_message"]["content"] == 'Trash project "Kitchen" and its 3 tasks?'

    provider = PeekingProvider([tool_calls_turn(trash), text_turn("Trashed it.")])
    _use(provider, trace_path)
    body = _post(client, conversation_id, "yes", DELEGATE).json()["assistant_message"]

    assert provider.sent == ["chat", "peek"]
    assert body["stop_reason"] == "completed"
    [entry] = body["record"]
    assert "count" not in entry  # the record's shape is main's (#520)
    assert body["content"] == f'Trashed project {project_id} "Kitchen" and its 3 tasks.'


def test_a_confirm_token_answer_is_the_code_built_note(
    client: TestClient, tool_db: sessionmaker[Session], trace_path: Path
) -> None:
    project_id = _kitchen(client)
    _use(ScriptedProvider([tool_calls_turn(("trash_project", {"project_id": project_id}))]), trace_path)
    conversation_id = _thread(client)
    asked = _post(client, conversation_id, "wipe the kitchen project", DELEGATE).json()
    token = asked["assistant_message"]["confirm_required"]["token"]
    _use(ScriptedProvider([]), trace_path)

    body = _post(
        client, conversation_id, "yes", {**DELEGATE, "Confirm-Token": token}
    ).json()["assistant_message"]

    assert body["content"] == f'Trashed project {project_id} "Kitchen" and its 3 tasks.'


# --- the note itself -------------------------------------------------------------


def _task(task_id: int, title: str, due: str | None = None) -> str:
    return json.dumps(
        {"id": task_id, "title": title, "workflow_status": "open", "due_date": due}
    )


CREATED: dict[str, Any] = {
    "tool": "create_task",
    "arguments": {"title": 'Sort "Honey" this weekend', "due_date": "2026-11-14"},
    "result": _task(52, 'Sort "Honey" this weekend', "2026-11-14"),
}
COMPLETED: dict[str, Any] = {
    "tool": "complete_task",
    "arguments": {"task_id": 41},
    "result": _task(41, "Pay rent"),
}
TRASHED: dict[str, Any] = {
    "tool": "trash_project",
    "arguments": {"project_id": 7},
    "result": 'Project 7 "Kitchen" and its 3 tasks moved to trash (undo with restore_project)',
}
REFUSED: dict[str, Any] = {
    "tool": "complete_task",
    "arguments": {"task_id": 39},
    "error": "task 39 is blocked by task 38, which is still open",
}


def test_the_note_for_a_create_quotes_the_stored_title() -> None:
    assert closing.note([CREATED]) == 'Created task 52 "Sort "Honey" this weekend", due 2026-11-14.'


def test_the_note_for_several_writes_names_each_in_order() -> None:
    assert closing.note([CREATED, COMPLETED, REFUSED]) == (
        'Created task 52 "Sort "Honey" this weekend", due 2026-11-14. '
        'Completed task 41 "Pay rent". '
        "Refused complete_task: task 39 is blocked by task 38, which is still open."
    )


def test_the_note_for_a_project_trash_says_what_went_with_it() -> None:
    assert closing.note([TRASHED]) == 'Trashed project 7 "Kitchen" and its 3 tasks.'


def test_the_note_for_a_project_restore_says_what_came_back() -> None:
    restored = {
        "tool": "restore_project",
        "arguments": {"project_id": 7, "restore_tasks": True},
        "result": json.dumps(
            {"project": {"id": 7, "name": "Kitchen", "closed_at": None}, "restored_task_count": 3}
        ),
    }
    assert closing.note([restored]) == 'Restored project 7 "Kitchen" and its 3 tasks.'


def test_every_note_is_backed_by_its_run() -> None:
    """The claim reader (#407) holds the note to the calls it was built from."""
    for calls in ([CREATED], [CREATED, COMPLETED, REFUSED], [TRASHED]):
        evidence = Evidence.of(
            [
                ToolCall(c["tool"], c["arguments"], c.get("result"), c.get("error"))
                for c in calls
            ],
            date.today(),
        )
        assert not unbacked(read_claims(closing.note(calls), evidence)), calls
