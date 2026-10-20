"""Agent loop end-to-end against a scripted provider (no GPU).

``ScriptedProvider`` plays the model: each ``chat()`` call pops the next
scripted turn (a ``ChatResult``, or an exception to raise), so the full loop
path — registry dispatch, Pydantic argument validation, session/actor
plumbing, self-correction — runs against the real service layer and an
in-memory database. The live model is exercised by the eval harness (slice 4),
not here.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import date

import pytest
import structlog
from sqlalchemy import Engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.ai import loop as loop_module
from app.ai.loop import LOOP_ACTOR, AgentLoop, AgentRunFailed, build_system_prompt
from app.ai.providers.llamacpp import (
    ChatResult,
    ProviderRequestError,
    ToolCall,
    ToolCallArgumentsError,
)
from app.db.models import ActivityEvent, Project, Task, TaskWorkflowStatus
from app.mcp.server import mcp
from app.tools import registry, runtime
from tests.scripted_provider import ScriptedProvider
from tests.scripted_provider import text_turn as _text
from tests.scripted_provider import tool_calls_turn as _calls


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    """Point the tool bodies' per-call sessions at the test engine."""
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def test_create_complete_flow_end_to_end(tool_db: sessionmaker[Session]) -> None:
    """create project → create task → complete, all audited as agent:loop."""
    provider = ScriptedProvider(
        [
            _calls(("create_project", {"data": {"name": "Agent epic"}})),
            _calls(("create_task", {"data": {"title": "Ship slice 1", "project_id": 1}})),
            _calls(("complete_task", {"task_id": 1})),
            _text("Created and completed 'Ship slice 1'."),
        ]
    )
    result = AgentLoop(provider).run(
        "Create a project 'Agent epic' with a task 'Ship slice 1' and finish it."
    )

    assert result.stop_reason == "completed"
    assert result.reply == "Created and completed 'Ship slice 1'."
    assert result.iterations == 4
    assert [record.tool for record in result.tool_calls] == [
        "create_project",
        "create_task",
        "complete_task",
    ]
    assert all(record.error is None for record in result.tool_calls)

    # Every provider turn was offered the full registry — and never a hard delete.
    offered = {spec.name for spec in provider.requests[0]["tools"]}
    assert {"create_task", "complete_task", "search", "restore_task"} <= offered
    assert not [name for name in offered if "purge" in name or "empty" in name]

    # Each tool result went back to the model as a role:tool message.
    followup = provider.requests[1]["messages"]
    assert followup[-1]["role"] == "tool"
    assert "Agent epic" in followup[-1]["content"]

    # DB end-state + audit rows.
    with tool_db() as db:
        task = db.execute(select(Task)).scalar_one()
        assert task.title == "Ship slice 1"
        assert task.workflow_status == TaskWorkflowStatus.done
        events = db.execute(select(ActivityEvent)).scalars().all()
        assert {event.action for event in events} >= {"created", "completed"}
        assert all(event.actor == LOOP_ACTOR for event in events)


def test_invalid_arguments_are_fed_back_and_corrected(
    tool_db: sessionmaker[Session],
) -> None:
    """A validation failure never reaches the service layer; the model retries."""
    provider = ScriptedProvider(
        [
            _calls(("create_project", {"data": {"name": "Fixups"}})),
            _calls(("create_task", {"data": {"title": "   ", "project_id": 1}})),
            _calls(("create_task", {"data": {"title": "Corrected", "project_id": 1}})),
            _text("Done."),
        ]
    )
    result = AgentLoop(provider).run("Add a task to Fixups.")

    assert result.stop_reason == "completed"
    failed = result.tool_calls[1]
    assert failed.result is None
    assert failed.error is not None and "title" in failed.error

    # The rejection reached the model as a tool-result error message…
    retry_request = provider.requests[2]["messages"]
    assert retry_request[-1]["role"] == "tool"
    assert "Invalid arguments" in retry_request[-1]["content"]

    # …and only the corrected call landed.
    with tool_db() as db:
        assert db.execute(select(Task.title)).scalars().all() == ["Corrected"]


def test_unparseable_tool_call_corrected_via_user_message(
    tool_db: sessionmaker[Session],
) -> None:
    """ToolCallArgumentsError from the provider is fed back, chess-style."""
    provider = ScriptedProvider(
        [
            ToolCallArgumentsError("create_task", "arguments are not valid JSON"),
            _text("Never mind."),
        ]
    )
    result = AgentLoop(provider).run("Add a task.")

    assert result.stop_reason == "completed"
    assert result.tool_calls == []  # nothing was dispatched
    retry_request = provider.requests[1]["messages"]
    assert retry_request[-1]["role"] == "user"
    assert "create_task" in retry_request[-1]["content"]


def test_domain_errors_do_not_consume_the_correction_budget(
    tool_db: sessionmaker[Session],
) -> None:
    """A well-formed call the service rejects is an ordinary observation."""
    provider = ScriptedProvider(
        [
            _calls(("complete_task", {"task_id": 99999})),
            _text("Task 99999 does not exist."),
        ]
    )
    # max_corrections=0 proves the not-found feedback isn't billed as a correction.
    result = AgentLoop(provider, max_corrections=0).run("Finish task 99999.")

    assert result.stop_reason == "completed"
    assert result.tool_calls[0].error is not None
    assert "not found" in result.tool_calls[0].error
    feedback = provider.requests[1]["messages"][-1]
    assert feedback["role"] == "tool"
    assert "not found" in feedback["content"]


def test_unknown_tool_is_a_schema_error(tool_db: sessionmaker[Session]) -> None:
    provider = ScriptedProvider(
        [
            _calls(("summon_intern", {})),
            _text("No such tool; stopping."),
        ]
    )
    result = AgentLoop(provider).run("Do something odd.")

    assert result.stop_reason == "completed"
    assert result.tool_calls[0].error is not None
    assert "Unknown tool" in result.tool_calls[0].error


def test_correction_limit_terminates_the_loop(tool_db: sessionmaker[Session]) -> None:
    provider = ScriptedProvider(
        [
            _calls(("create_task", {"data": {"title": ""}})),
            _calls(("create_task", {"data": {"title": ""}})),
        ]
    )
    result = AgentLoop(provider, max_corrections=1).run("Add a task.")

    assert result.stop_reason == "correction_limit"
    assert result.reply is None
    assert len(provider.requests) == 2
    with tool_db() as db:
        assert db.execute(select(Task)).scalars().all() == []


def test_max_iterations_terminates_the_loop(tool_db: sessionmaker[Session]) -> None:
    # Two different lookups, so the stall rule (#398) never fires first.
    provider = ScriptedProvider(
        [
            _calls(("list_projects", {})),
            _calls(("list_tasks", {})),
        ]
    )
    result = AgentLoop(provider, max_iterations=2).run("Browse forever.")

    assert result.stop_reason == "max_iterations"
    assert result.iterations == 2
    assert len(provider.requests) == 2


def test_a_turn_that_only_repeats_itself_ends_the_run_no_progress(
    tool_db: sessionmaker[Session],
) -> None:
    """Every call this turn repeats an exchange the run already saw (#398):
    the run ends ``no_progress`` with no reply, instead of spending the rest
    of the iteration budget re-reading the same thing."""
    provider = ScriptedProvider(
        [
            _calls(("list_projects", {}), ("search", {"query": "garage"})),
            # An exchange the run has already seen, answered the same way.
            _calls(("search", {"query": "garage"})),
            _calls(("list_projects", {})),  # never requested: the run ended
        ]
    )
    result = AgentLoop(provider).run("What's in my garage project?")

    assert result.stop_reason == "no_progress"
    assert result.reply is None
    assert result.iterations == 2
    assert len(provider.requests) == 2
    assert [record.tool for record in result.tool_calls] == [
        "list_projects",
        "search",
        "search",
    ]
    # The transcript stays well-formed: it ends on the repeated call's answer.
    assert result.messages[-1]["role"] == "tool"


def test_exchange_key_ignores_argument_order() -> None:
    assert loop_module._exchange_key(
        "list_tasks", {"project_id": 1, "status": "todo"}, "[]"
    ) == loop_module._exchange_key("list_tasks", {"status": "todo", "project_id": 1}, "[]")


def test_a_repeated_call_that_answers_differently_is_progress(
    tool_db: sessionmaker[Session],
) -> None:
    """The repeat is keyed on the answer too: the same lookup after a write
    comes back different, so the run goes on to its reply."""
    provider = ScriptedProvider(
        [
            _calls(("list_projects", {})),
            _calls(("create_project", {"data": {"name": "Garage"}})),
            _calls(("list_projects", {})),  # same call, new answer
            _text("Created 'Garage'; it's your only project."),
        ]
    )
    result = AgentLoop(provider).run("Make a Garage project and list my projects.")

    assert result.stop_reason == "completed"
    assert result.reply == "Created 'Garage'; it's your only project."
    assert [record.tool for record in result.tool_calls] == [
        "list_projects",
        "create_project",
        "list_projects",
    ]


def test_a_turn_with_one_new_exchange_is_progress(
    tool_db: sessionmaker[Session],
) -> None:
    """Only a turn in which *every* call repeats is a stall."""
    provider = ScriptedProvider(
        [
            _calls(("list_projects", {})),
            _calls(("list_projects", {}), ("list_tasks", {})),
            _text("Nothing here yet."),
        ]
    )
    result = AgentLoop(provider).run("What do I have?")

    assert result.stop_reason == "completed"
    assert len(result.tool_calls) == 3


def test_tool_call_cap_answers_excess_calls_not_run_and_never_dispatches_them(
    tool_db: sessionmaker[Session],
) -> None:
    """Past the per-run cap (#398) a call is answered "not run" — one answer
    per call — never reaches the service layer, and the run ends
    ``tool_call_limit`` after that turn."""
    provider = ScriptedProvider(
        [
            _calls(("create_project", {"data": {"name": "P1"}})),
            _calls(
                ("create_project", {"data": {"name": "P2"}}),
                ("create_project", {"data": {"name": "P3"}}),
                ("create_project", {"data": {"name": "P4"}}),
            ),
            _text("never requested"),
        ]
    )
    result = AgentLoop(provider, max_tool_calls=2).run("Make four projects.")

    assert result.stop_reason == "tool_call_limit"
    assert result.reply is None
    assert result.iterations == 2
    assert len(provider.requests) == 2
    assert [record.error is None for record in result.tool_calls] == [
        True,
        True,
        False,
        False,
    ]
    for refused in result.tool_calls[2:]:
        assert refused.error == loop_module._TOOL_CALL_LIMIT_ERROR
    # One role:tool answer per call of the capped turn, in order.
    turn = next(
        m for m in reversed(result.messages) if m.get("tool_calls")
    )
    answers = result.messages[result.messages.index(turn) + 1 :]
    assert [m["tool_call_id"] for m in answers] == [
        c["id"] for c in turn["tool_calls"]
    ]
    assert all("not run" in m["content"] for m in answers[1:])
    # The refused calls never ran: no project, no audit row.
    with tool_db() as db:
        assert sorted(db.execute(select(Project.name)).scalars().all()) == ["P1", "P2"]
        created = db.execute(
            select(ActivityEvent).where(ActivityEvent.action == "created")
        ).scalars().all()
        assert len(created) == 2


def test_default_tool_call_cap_fits_the_largest_deployed_batch() -> None:
    # The largest deployed run made 18 calls (#398).
    assert loop_module.DEFAULT_MAX_TOOL_CALLS >= 18
    with pytest.raises(ValueError):
        AgentLoop(ScriptedProvider([]), max_tool_calls=0)


def test_every_model_call_is_capped(tool_db: sessionmaker[Session]) -> None:
    """``max_tokens`` goes out on every provider call (#397)."""
    provider = ScriptedProvider(
        [_calls(("list_projects", {})), _text("No projects yet.")]
    )
    AgentLoop(provider).run("What projects do I have?")
    AgentLoop(provider_capped := ScriptedProvider([_text("Hi.")]), max_tokens=64).run(
        "Hello."
    )

    assert [request["max_tokens"] for request in provider.requests] == [
        loop_module.DEFAULT_MAX_TOKENS,
        loop_module.DEFAULT_MAX_TOKENS,
    ]
    assert loop_module.DEFAULT_MAX_TOKENS == 2048
    assert provider_capped.requests[0]["max_tokens"] == 64


def test_cut_off_text_turn_is_dropped_and_ends_truncated(
    tool_db: sessionmaker[Session],
) -> None:
    """A text turn the cap cut off is not a reply: no reply, no fragment kept."""
    fragment = "You have three projects: Alpha, Beta, and the the the the"
    provider = ScriptedProvider(
        [
            _calls(("list_projects", {})),
            ChatResult(
                content=fragment, tool_calls=[], finish_reason="length", usage=None
            ),
        ]
    )
    result = AgentLoop(provider).run("What projects do I have?")

    assert result.stop_reason == "truncated"
    assert result.reply is None
    assert result.iterations == 2
    # The lookup that ran stays on the record.
    assert [record.tool for record in result.tool_calls] == ["list_projects"]
    # The fragment never reaches the transcript.
    assert all(fragment not in str(message.get("content")) for message in result.messages)
    assert result.messages[-1]["role"] == "tool"


def test_cut_off_turn_with_tool_calls_still_runs_them(
    tool_db: sessionmaker[Session],
) -> None:
    """Calls in a cut-off turn arrived whole, so they run; the text is dropped."""
    fragment = "Creating the project now, and then I will also"
    provider = ScriptedProvider(
        [
            ChatResult(
                content=fragment,
                tool_calls=[
                    ToolCall(
                        id="call_0",
                        name="create_project",
                        arguments={"data": {"name": "Cut-off epic"}},
                    )
                ],
                finish_reason="length",
                usage=None,
            ),
            _text("Created 'Cut-off epic'."),
        ]
    )
    result = AgentLoop(provider).run("Create a project 'Cut-off epic'.")

    assert result.stop_reason == "completed"
    assert result.reply == "Created 'Cut-off epic'."
    assert [record.tool for record in result.tool_calls] == ["create_project"]
    assert result.tool_calls[0].error is None
    # The second request carries the call and its result, not the fragment.
    sent = provider.requests[1]["messages"]
    assistant_turn = next(m for m in sent if m.get("tool_calls"))
    assert assistant_turn["content"] is None
    assert all(fragment not in str(message.get("content")) for message in sent)
    with tool_db() as db:
        assert db.execute(select(Project.name)).scalars().all() == ["Cut-off epic"]


def test_provider_failure_mid_run_keeps_the_partial_trajectory(
    tool_db: sessionmaker[Session],
) -> None:
    """A tool runs, then the provider fails: AgentRunFailed carries the tool
    that already ran, tagged provider_error → 502."""
    provider = ScriptedProvider(
        [
            _calls(("create_task", {"data": {"title": "Ran before the fall"}})),
            ProviderRequestError("boom"),
        ]
    )
    with pytest.raises(AgentRunFailed) as exc_info:
        AgentLoop(provider).run("Do a thing")

    failed = exc_info.value
    assert failed.http_status == 502
    assert failed.result.stop_reason == "provider_error"
    assert failed.result.reply is None
    assert [r.tool for r in failed.result.tool_calls] == ["create_task"]
    assert failed.result.tool_calls[0].error is None  # the tool itself succeeded


def test_passed_deadline_times_out_before_calling_the_provider(
    tool_db: sessionmaker[Session],
) -> None:
    """An already-expired deadline stops the run before any provider call —
    timed_out → 504, and the provider is never asked (empty script)."""
    provider = ScriptedProvider([])
    with pytest.raises(AgentRunFailed) as exc_info:
        AgentLoop(provider).run("Too late", deadline=time.monotonic() - 1.0)

    assert exc_info.value.http_status == 504
    assert exc_info.value.result.stop_reason == "timed_out"
    assert provider.requests == []


def test_deadline_caps_the_provider_call_timeout(
    tool_db: sessionmaker[Session],
) -> None:
    """The loop passes the time remaining as each call's timeout, so a single
    call can't outlive the budget."""
    provider = ScriptedProvider([_text("done")])
    AgentLoop(provider).run("Hi", deadline=time.monotonic() + 30.0)

    passed_timeout = provider.requests[0]["timeout"]
    assert passed_timeout is not None and 0.0 < passed_timeout <= 30.0


def _fake_clock(
    monkeypatch: pytest.MonkeyPatch, readings: list[float]
) -> None:
    """Make ``loop.time.monotonic`` return ``readings`` in order (last sticks)."""
    remaining = list(readings)

    def monotonic() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    monkeypatch.setattr("app.ai.loop.time.monotonic", monotonic)


def test_deadline_expiring_before_dispatch_skips_the_tool_call(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider answers in time but the deadline passes before dispatch:
    the tool never runs, and the run ends timed_out → 504 (#108)."""
    provider = ScriptedProvider([_calls(("create_task", {"data": {"title": "Too late"}}))])
    # Readings: iteration check, provider-timeout, then past the deadline.
    _fake_clock(monkeypatch, [0.0, 0.0, 100.0])

    with pytest.raises(AgentRunFailed) as exc_info:
        AgentLoop(provider).run("Make a task", deadline=10.0)

    failed = exc_info.value
    assert failed.http_status == 504
    assert failed.result.stop_reason == "timed_out"
    skipped = failed.result.tool_calls
    assert [r.tool for r in skipped] == ["create_task"]
    assert skipped[0].result is None
    assert skipped[0].error is not None and "time budget" in skipped[0].error
    # The transcript still answers the call, and nothing reached the DB.
    assert failed.result.messages[-1]["role"] == "tool"
    with tool_db() as db:
        assert db.execute(select(Task)).scalars().all() == []


def test_batch_stops_at_the_call_that_would_cross_the_deadline(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A three-call batch that crosses the deadline runs only the calls that
    started in time; the rest are recorded as not run (#108)."""
    provider = ScriptedProvider(
        [
            _calls(
                ("create_task", {"data": {"title": "First"}}),
                ("create_task", {"data": {"title": "Second"}}),
                ("create_task", {"data": {"title": "Third"}}),
            )
        ]
    )
    # Readings: iteration check, provider-timeout, call 1 in time, then expired.
    _fake_clock(monkeypatch, [0.0, 0.0, 0.0, 100.0])

    with pytest.raises(AgentRunFailed) as exc_info:
        AgentLoop(provider).run("Make three tasks", deadline=10.0)

    result = exc_info.value.result
    assert result.stop_reason == "timed_out"
    assert [r.error is None for r in result.tool_calls] == [True, False, False]
    assert all("time budget" in str(r.error) for r in result.tool_calls[1:])
    # Every tool call got a role:tool message, so the transcript stays valid.
    assert sum(1 for m in result.messages if m["role"] == "tool") == 3
    with tool_db() as db:
        assert db.execute(select(Task.title)).scalars().all() == ["First"]


def _raise_on_tool(
    monkeypatch: pytest.MonkeyPatch, tool: str, exc: Exception
) -> None:
    """Make registry dispatch of ``tool`` blow up, leaving other tools alone."""
    real = registry.call_tool

    def call_tool(name: str, arguments: dict[str, object], *, actor: str) -> object:
        if name == tool:
            raise exc
        return real(name, arguments, actor=actor)

    monkeypatch.setattr(registry, "call_tool", call_tool)


def test_unexpected_tool_exception_fails_the_run_with_the_partial_trajectory(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One tool completes, the next one's dispatch raises RuntimeError: the run
    ends as AgentRunFailed/internal_error → 500 carrying both records (#103)."""
    provider = ScriptedProvider(
        [
            _calls(("create_task", {"data": {"title": "Ran fine"}})),
            _calls(("complete_task", {"task_id": 1})),
            _text("never reached"),
        ]
    )
    _raise_on_tool(monkeypatch, "complete_task", RuntimeError("disk on fire"))

    with pytest.raises(AgentRunFailed) as exc_info:
        AgentLoop(provider).run("Create then complete a task")

    failed = exc_info.value
    assert failed.http_status == 500
    assert failed.result.stop_reason == "internal_error"
    assert failed.result.reply is None
    assert [r.tool for r in failed.result.tool_calls] == ["create_task", "complete_task"]
    assert failed.result.tool_calls[0].error is None
    crashed = failed.result.tool_calls[1]
    assert crashed.result is None
    assert crashed.error is not None and "internal error" in crashed.error
    # The transcript stays well-formed: both calls have a role:tool answer.
    assert sum(1 for m in failed.result.messages if m["role"] == "tool") == 2
    # The first tool's write stays committed (undoable via trash).
    with tool_db() as db:
        assert db.execute(select(Task.title)).scalars().all() == ["Ran fine"]
    # The provider was never asked for another turn.
    assert len(provider.requests) == 2


def test_database_failure_during_dispatch_becomes_an_internal_error(
    tool_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OperationalError (e.g. the SQLite lock case) is an internal_error,
    not a raw exception escaping the loop boundary (#103)."""
    provider = ScriptedProvider([_calls(("list_projects", {}))])
    _raise_on_tool(
        monkeypatch, "list_projects", OperationalError("SELECT 1", {}, Exception("locked"))
    )

    with pytest.raises(AgentRunFailed) as exc_info:
        AgentLoop(provider).run("List projects")

    assert exc_info.value.http_status == 500
    assert exc_info.value.result.stop_reason == "internal_error"


def test_domain_and_schema_errors_keep_their_existing_behavior(
    tool_db: sessionmaker[Session],
) -> None:
    """A domain rejection is still fed back (not internal_error), and the run
    can recover and finish normally."""
    provider = ScriptedProvider(
        [
            _calls(("complete_task", {"task_id": 999})),
            _text("That task doesn't exist."),
        ]
    )
    result = AgentLoop(provider).run("Complete task 999")

    assert result.stop_reason == "completed"
    assert result.tool_calls[0].error is not None
    assert "internal error" not in result.tool_calls[0].error


def test_tool_session_reuses_a_bound_request_id(
    tool_db: sessionmaker[Session],
) -> None:
    """The loop binds one request ID per run; tool calls must not clobber it."""
    structlog.contextvars.bind_contextvars(request_id="run-1")
    try:
        with runtime.tool_session("list_projects"):
            assert structlog.contextvars.get_contextvars()["request_id"] == "run-1"
        assert structlog.contextvars.get_contextvars()["request_id"] == "run-1"
    finally:
        structlog.contextvars.unbind_contextvars("request_id")


def test_system_prompt_layers_compose_in_order() -> None:
    """app base → global Glitch → date, in that order, all present."""
    prompt = build_system_prompt(date(2026, 7, 11))

    # Layer 1: the app behavioral contract.
    assert "Project Command Center (PCC)" in prompt
    assert "never guess an id" in prompt
    assert "never invent" in prompt
    # Layer 2: the vendored house personality.
    assert "you are Glitch" in prompt
    # Layer 3: the dynamic date injection.
    assert "Today's date is 2026-07-11." in prompt

    base_at = prompt.index("You act only by calling the provided tools.")
    glitch_at = prompt.index("you are Glitch")
    date_at = prompt.index("Today's date is 2026-07-11.")
    assert base_at < glitch_at < date_at


def test_global_personality_is_the_vendored_glitch_without_its_header() -> None:
    """The vendored ``<!-- vendored -->`` line is stripped; the body survives."""
    personality = loop_module._GLOBAL_PERSONALITY

    assert not personality.startswith("<!--")
    assert "<!-- vendored" not in personality
    assert personality.startswith("Your personality: you are Glitch")
    # The brevity/honesty contract that must not be lost when Glitch is layered in.
    assert "The character never overrides the job" in personality


def test_registry_and_mcp_expose_identical_tools() -> None:
    """The registry is the single source of truth: same names, same schemas."""
    # _tool_manager is FastMCP-internal, but this is exactly the parity the
    # refactor must guarantee, so the test reaches in.
    mcp_tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    specs = {spec.name: spec for spec in registry.tool_specs()}

    assert set(specs) == set(mcp_tools)
    for name, spec in specs.items():
        assert spec.parameters == mcp_tools[name].parameters, name
        assert spec.description  # never advertise an undocumented tool
