"""Seeded agent-loop trajectories, in CI (#409).

``trajectory.py`` is the machinery: a seeded walk of user messages through the
real route, loop and registry, a scripted provider, injected faults, and every
invariant checked after every step. This file is what runs:

- **The corpus.** ``PCC_TRAJ_SEEDS`` walks (default 32) of
  ``PCC_TRAJ_LENGTH`` messages (default ``CORPUS_LENGTH``), from seed
  ``PCC_TRAJ_SEED_BASE`` (default 0). A soak:
  ``PCC_TRAJ_SEEDS=2000 PCC_TRAJ_SEED_BASE=10000 .venv/bin/pytest
  tests/test_trajectories.py -q``.
- **Named cases.** Short fixed step sequences, each tagged: ``known-bug:<name>``
  reproduces an entry of ``KNOWN_BUGS`` strictly (an xfail until the fix
  lands, when it XPASSes and must be untagged along with the entry), or
  ``prevents`` (a combination the corpus reaches only now and then, pinned).
- **The instrument's own checks.** Every invariant must be able to fail, every
  registered tool must be walked, and a walk must be a pure function of its
  seed. A checker that cannot fire would read green for having looked at
  nothing.
- **Replay.** ``PCC_TRAJ_SEED=<n> PCC_TRAJ_LENGTH=<k> .venv/bin/pytest
  tests/test_trajectories.py -k replay -s`` reruns one walk and prints it —
  the command every breach report carries.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from app.ai.loop import LOOP_ACTOR, AgentRunResult, ToolCallRecord
from app.tools import registry
from tests.trajectory import (
    CORPUS_LENGTH,
    INVARIANTS,
    KNOWN_BUGS,
    TOOL_WEIGHTS,
    UNSKIP_PURGES_ROWS,
    ChatEvent,
    Fault,
    InvariantBreach,
    Observed,
    Step,
    ToolEvent,
    Turn,
    check_every_call_has_one_answer,
    check_every_mutation_is_audited,
    check_fault_ends_the_run_as_documented,
    check_nothing_is_hard_deleted,
    check_nothing_starts_after_stop_or_deadline,
    check_saved_turn_is_what_ran,
    check_trashed_is_restorable,
    describe,
    empty_snapshot,
    run_steps,
    walk,
)

CORPUS_SEEDS = int(os.environ.get("PCC_TRAJ_SEEDS", "32"))
SEED_BASE = int(os.environ.get("PCC_TRAJ_SEED_BASE", "0"))
LENGTH = int(os.environ.get("PCC_TRAJ_LENGTH", str(CORPUS_LENGTH)))


@pytest.mark.parametrize("seed", range(SEED_BASE, SEED_BASE + CORPUS_SEEDS))
def test_corpus_walk_keeps_every_invariant(seed: int) -> None:
    walk(seed, LENGTH)


# --- named cases -----------------------------------------------------------------------

# The world every walk starts on (``Harness._seed_world``): projects General 1,
# Home 2, Work 3; tasks "Buy paint" 1 (Home) with subtask "Pick a colour" 2,
# "Draft the plan" 3 and "Ship it" 4 (Work), 4 waiting on 3, and "Water plants"
# 5 (Home, daily); focus entries: a pin of task 1 (1) and a daily "Standup" (2).


def calls(*batch: tuple[str, dict[str, Any]]) -> Turn:
    return Turn("calls", calls=tuple(batch))


def reply(text: str = "Done.") -> Turn:
    return Turn("text", text=text)


def _create(title: str) -> tuple[str, dict[str, Any]]:
    return "create_task", {"data": {"title": title, "project_id": 2}}


_RECURRING = (
    "update_task",
    {
        "task_id": 3,
        "changes": {"repeat_interval": {"unit": "day", "every": 1}, "due_date": "2026-01-05"},
    },
)

NAMED: dict[str, tuple[str, tuple[Step, ...]]] = {
    # SQLite fails inside the first call of a three-call batch: the run ends
    # internal_error, and the two calls after it are recorded and answered as
    # not run.
    "db_error_mid_batch": (
        "prevents",
        (
            Step(
                (calls(_create("One"), _create("Two"), _create("Three")), reply()),
                fault=Fault("db_error", "tool", 0, statement=3),
            ),
        ),
    ),
    # The provider raises a non-ProviderError after a call already ran: the
    # run ends internal_error, and the saved turn and the trace keep the call.
    "provider_crash_after_a_write": (
        "prevents",
        (Step((calls(_create("Ran")), reply()), fault=Fault("provider_crashes", "chat", 1)),),
    ),
    # Skip a recurring occurrence, then restore it: the un-skip rewind purges
    # the skipped row.
    "restore_a_skipped_occurrence": (
        f"known-bug:{UNSKIP_PURGES_ROWS}",
        (
            Step((calls(_RECURRING), calls(("skip_occurrence", {"task_id": 3})), reply())),
            Step((calls(("restore_task", {"task_id": 3})), reply())),
        ),
    ),
    # Stop pressed while the limit summary is being written: it completes (it
    # was in flight), the stop reason stays the limit's.
    "stop_during_the_summary": (
        "prevents",
        (
            Step(
                (calls(_create("A")), calls(("list_tasks", {})), reply()),
                fault=Fault("stop", "chat", 2),
                max_iterations=2,
            ),
        ),
    ),
    # The deadline passes during the second call of a batch: the third never
    # starts, is answered "not run", and nothing is summarised.
    "deadline_mid_batch": (
        "prevents",
        (
            Step(
                (calls(_create("A"), _create("B"), _create("C")), reply()),
                fault=Fault("deadline", "tool", 1),
            ),
        ),
    ),
    # Stop during the last call of a turn that only repeats a lookup: the run
    # ends at the stall rule, and no summary starts after the Stop.
    "stop_in_a_stalled_turn": (
        "prevents",
        (
            Step(
                (calls(("list_projects", {})), calls(("list_projects", {})), reply()),
                fault=Fault("stop", "tool", 1),
            ),
        ),
    ),
    # SQLite refuses the BEGIN of a write: nothing is written, the run 500s
    # with the call recorded.
    "db_error_on_begin": (
        "prevents",
        (Step((calls(_create("Never")), reply()), fault=Fault("db_error", "tool", 0)),),
    ),
    # A trashed project restored by the agent, its tasks with it, after a
    # schema correction, as a delegate.
    "trash_and_restore_a_project_as_a_delegate": (
        "prevents",
        (
            Step(
                (
                    calls(("trash_project", {"project_id": 3})),
                    calls(("create_project", {"data": {"title": "Wrong key"}})),
                    calls(("restore_project", {"project_id": 3, "restore_tasks": True})),
                    reply(),
                ),
                actor_header="agent:conductor",
            ),
        ),
    ),
}


def _named(name: str) -> Any:
    tag, _ = NAMED[name]
    if tag.startswith("known-bug:"):
        return pytest.param(
            name,
            marks=pytest.mark.xfail(
                raises=InvariantBreach, strict=True, reason=KNOWN_BUGS[tag.split(":", 1)[1]]
            ),
        )
    return name


@pytest.mark.parametrize("name", [_named(name) for name in sorted(NAMED)])
def test_named_case(name: str) -> None:
    _, steps = NAMED[name]
    run_steps(steps)


def test_every_named_case_says_what_it_is() -> None:
    for tag, _ in NAMED.values():
        assert tag == "prevents" or tag.split(":", 1)[1] in KNOWN_BUGS
    # Every known bug has a case that reproduces it.
    assert {tag.split(":", 1)[1] for tag, _ in NAMED.values() if tag != "prevents"} == set(
        KNOWN_BUGS
    )


def test_a_known_bug_passes_the_corpus_but_not_a_named_case() -> None:
    """The tolerance is the corpus's alone: the same steps, tolerant, pass."""
    _, steps = NAMED["restore_a_skipped_occurrence"]
    log = run_steps(steps, tolerate_known_bugs=True)
    # Noted once per purged row; the restore is the second step.
    assert log[0].known_bugs == []
    assert set(log[1].known_bugs) == {UNSKIP_PURGES_ROWS}


# --- the instrument's own checks ----------------------------------------------------------


def _run(*records: ToolCallRecord, stop: str = "completed", **fields: Any) -> AgentRunResult:
    return AgentRunResult(
        reply=fields.pop("reply", "Done."),
        stop_reason=stop,  # type: ignore[arg-type]
        tool_calls=list(records),
        iterations=1,
        messages=fields.pop("messages", []),
    )


def _tool(name: str = "create_task", **fields: Any) -> ToolEvent:
    defaults: dict[str, Any] = {
        "index": 0,
        "name": name,
        "arguments": {},
        "actor": LOOP_ACTOR,
        "after_stop": False,
        "after_deadline": False,
        "before": empty_snapshot(),
        "after": empty_snapshot(),
    }
    return ToolEvent(**(defaults | fields))


def _chat(**fields: Any) -> ChatEvent:
    defaults: dict[str, Any] = {
        "index": 0,
        "summary": False,
        "after_stop": False,
        "after_deadline": False,
        "messages": [],
    }
    return ChatEvent(**(defaults | fields))


_STEP = Step((reply(),))


def _with_row(table: str, row_id: int, **row: Any) -> dict[str, dict[int, dict[str, Any]]]:
    snap = empty_snapshot()
    snap[table][row_id] = {"id": row_id, **row}
    return snap


def test_the_answer_check_fires_on_an_unanswered_call() -> None:
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
        {"role": "user", "content": "next"},
    ]
    with pytest.raises(InvariantBreach, match="answered by"):
        check_every_call_has_one_answer(Observed(_STEP, chats=[_chat(messages=messages)]))


def test_the_answer_check_fires_on_an_orphan_answer() -> None:
    messages = [{"role": "tool", "tool_call_id": "a", "content": "ok"}]
    with pytest.raises(InvariantBreach, match="no call"):
        check_every_call_has_one_answer(Observed(_STEP, result=_run(messages=messages)))


def test_the_saved_turn_check_fires_on_a_turn_that_hides_a_call() -> None:
    record = ToolCallRecord(tool="create_task", arguments={}, result="{}")
    observed = Observed(
        _STEP,
        result=_run(record),
        tools=[_tool()],
        saved=[
            {"role": "user"},
            {
                "role": "assistant",
                "content": "Done.",
                "stop_reason": "completed",
                "tool_calls": None,
            },
        ],
    )
    with pytest.raises(InvariantBreach, match="saved tool calls"):
        check_saved_turn_is_what_ran(observed)


def test_the_saved_turn_check_fires_on_a_call_the_record_misses() -> None:
    observed = Observed(
        _STEP,
        result=_run(),
        tools=[_tool()],
        saved=[
            {"role": "user"},
            {
                "role": "assistant",
                "content": "Done.",
                "stop_reason": "completed",
                "tool_calls": None,
            },
        ],
    )
    with pytest.raises(InvariantBreach, match="the registry ran"):
        check_saved_turn_is_what_ran(observed)


def test_the_audit_check_fires_on_an_unlogged_write() -> None:
    tool = _tool(after=_with_row("tasks", 7, project_id=2))
    with pytest.raises(InvariantBreach, match="no activity event"):
        check_every_mutation_is_audited(Observed(_STEP, tools=[tool]))


def test_the_audit_check_fires_on_an_event_in_someone_elses_name() -> None:
    after = _with_row("tasks", 7, project_id=2)
    after["activity_events"][1] = {
        "id": 1,
        "entity_type": "task",
        "entity_id": 7,
        "action": "created",
        "actor": None,
    }
    with pytest.raises(InvariantBreach, match="someone other than"):
        check_every_mutation_is_audited(Observed(_STEP, tools=[_tool(after=after)]))


def test_the_audit_check_fires_on_a_refused_call_that_wrote() -> None:
    tool = _tool(after=_with_row("projects", 3), raised=ValueError("refused"))
    with pytest.raises(InvariantBreach, match="but wrote"):
        check_every_mutation_is_audited(Observed(_STEP, tools=[tool]))


def test_the_hard_delete_check_fires_on_a_missing_row() -> None:
    observed = Observed(_STEP, before=_with_row("tasks", 4, deleted_at=None))
    with pytest.raises(InvariantBreach, match="hard-deleted"):
        check_nothing_is_hard_deleted(observed)


def test_the_hard_delete_check_fires_on_a_rewritten_audit_row() -> None:
    observed = Observed(
        _STEP,
        before=_with_row("activity_events", 1, action="created"),
        after=_with_row("activity_events", 1, action="deleted"),
    )
    with pytest.raises(InvariantBreach, match="rewritten"):
        check_nothing_is_hard_deleted(observed)


def test_the_restore_check_fires_on_an_unrestorable_row() -> None:
    with pytest.raises(InvariantBreach, match="not restorable"):
        check_trashed_is_restorable(Observed(_STEP, unrestorable=["task 3: boom"]))


def test_the_start_check_fires_on_a_call_after_stop_or_the_deadline() -> None:
    with pytest.raises(InvariantBreach, match="after Stop"):
        check_nothing_starts_after_stop_or_deadline(
            Observed(_STEP, chats=[_chat(summary=True, after_stop=True)])
        )
    with pytest.raises(InvariantBreach, match="after the deadline"):
        check_nothing_starts_after_stop_or_deadline(
            Observed(_STEP, tools=[_tool(after_deadline=True)])
        )


def test_the_fault_check_fires_on_a_stop_that_did_not_stop() -> None:
    step = Step((reply(),), fault=Fault("stop", "chat", 0))
    observed = Observed(
        step,
        result=_run(),
        chats=[_chat(fault=True), _chat(index=1)],
    )
    with pytest.raises(InvariantBreach, match="ended completed"):
        check_fault_ends_the_run_as_documented(observed)


def test_the_fault_check_fires_on_a_db_error_that_did_not_fail_the_run() -> None:
    step = Step((reply(),), fault=Fault("db_error", "tool", 0))
    observed = Observed(step, result=_run(), tools=[_tool(fault=True)], chats=[_chat()])
    with pytest.raises(InvariantBreach, match="ended completed"):
        check_fault_ends_the_run_as_documented(observed)


def test_every_invariant_is_run() -> None:
    names = {invariant.__name__ for invariant in INVARIANTS}
    assert len(INVARIANTS) == len(names)
    assert names == {
        "check_every_call_has_one_answer",
        "check_saved_turn_is_what_ran",
        "check_every_mutation_is_audited",
        "check_nothing_is_hard_deleted",
        "check_trashed_is_restorable",
        "check_nothing_starts_after_stop_or_deadline",
        "check_fault_ends_the_run_as_documented",
    }


def test_every_registered_tool_is_walked() -> None:
    """A new tool joins the walk the day it is registered."""
    assert set(TOOL_WEIGHTS) == {tool.name for tool in registry.all_tools()}


def test_a_walk_is_a_pure_function_of_its_seed() -> None:
    assert list(describe(walk(7, 6))) == list(describe(walk(7, 6)))


# --- replay ----------------------------------------------------------------------------------


@pytest.mark.skipif("PCC_TRAJ_SEED" not in os.environ, reason="set PCC_TRAJ_SEED to replay")
def test_replay() -> None:
    seed = int(os.environ["PCC_TRAJ_SEED"])
    print(f"\nseed={seed} length={LENGTH}")
    for line in describe(walk(seed, LENGTH)):
        print(line)
