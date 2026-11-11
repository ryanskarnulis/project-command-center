"""The conductor-phrased tier (#514): what conductor's planner really sends, measured, not gated.

The gate (``tests/test_agent_evals.py``) asks in human phrasing. Conductor does
not: it passes the person's words through, fills ``{sN}`` with a quoted title
from another app, and appends a saved fact as quoted context (conductor#134).
These scenarios send those compiled ``request`` strings with
``X-Agent-Actor: agent:conductor`` and check the end state and the typed record
the route returns, as the gate's delegate scenarios do. No live fleet is
needed: the requests are copied in, not generated.

Opt-in like the gate, never in CI (it needs the model):

    cd backend
    PCC_AGENT_CONDUCTOR_EVALS=1 PCC_AGENT_EVALS_REPORT=/tmp/conductor.jsonl \\
        .venv/bin/pytest tests/test_agent_conductor_evals.py -v -s

Same sampling, knobs and report as the gate (``PCC_AGENT_EVALS_RUNS``,
``_SEED``, ``_REPORT``, ...). A count below the reference floor (0.8) is
printed and reported, never failed: a miss here becomes an issue, not a fix in
this change. A scenario still fails when its number could not be measured (an
infrastructure abort, a request llama-server rejected, a bug in a check).

The request strings below come from conductor at commit 70476ea
(``backend/tests/plan_cases.py``, ``backend/tests/session_scenarios.py`` S2,
S3, S9, S15 and M3, and ``docs/agent-evals.md`` "Fleet smoke", which records
the words PCC received from the real planner). There is no sync: when
conductor's phrasing moves, re-copy and update the commit named here.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import date, timedelta

import pytest

from app.services import projects as projects_service
from app.services import tasks as tasks_service
from tests.eval_threads import weekend_targets
from tests.test_agent_evals import (
    Sample,
    Thread,
    _assert_completed,
    _assert_task_changes,
    _gate,
    _new_task,
    _successful,
    eval_session,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("PCC_AGENT_CONDUCTOR_EVALS") != "1",
    reason="the conductor-phrased tier runs the real model: set PCC_AGENT_CONDUCTOR_EVALS=1",
)

TIER = "conductor"
CONDUCTOR = "agent:conductor"
HEADERS = {"X-Agent-Actor": CONDUCTOR}

# --- conductor's compiled requests (conductor 70476ea) -----------------------------
#
# S2: the person's words, passed through untouched.
S2_REQUEST = "add a task to water the plants tomorrow"
# S3's second step, ``add a task to sort {s1} this weekend``, with ``{s1}``
# filled from music's receipt: the album's title, in curly quotes
# (``dataflow.from_record``). Music's own "Honey" is the fake world's album; the
# real music app names none (conductor's Fleet smoke), so a quoted title is
# what the fake planner path sends.
S3_REQUEST = "add a task to sort “Honey” this weekend"
# The wording the real planner used once in the fleet smoke run.
S3_ALBUM_REQUEST = "add a task to sort the album “Honey” this weekend"
# S15's second turn, as the real planner sent it when "their other album" had
# nothing to resolve against: no quoted title at all.
S15_REQUEST = "add a task to sort the other Caribou album this weekend"
# S9: the person's words; the app asks, and a "yes" confirms.
S9_REQUEST = "wipe the kitchen project"
S9_ANSWER = "yes"
# M3 (#134): the planner's request, then ``facts.attach``'s line with the saved
# fact, quoted and marked as context, not as part of the request.
M3_FACT = "I sort music on Sundays"
M3_REQUEST = (
    "add a task for my music sorting day to clean the record player"
    "\n\n(Context from conductor, not part of the request: what the person asked "
    f'conductor to remember, in their own words: "{M3_FACT}")'
)


@pytest.fixture(scope="module", autouse=True)
def _conductor_session() -> Generator[None, None, None]:
    with eval_session(TIER):
        yield


# --- the world and the record ------------------------------------------------------


def _seed_fleet_world(sample: Sample) -> int:
    """The projects conductor's fleet smoke seeds PCC with: Household, Garage
    and Kitchen (three open tasks). Returns Kitchen's id."""
    with sample.db() as db:
        projects_service.create_project(db, name="Household")
        projects_service.create_project(db, name="Garage")
        kitchen = projects_service.create_project(db, name="Kitchen")
        for title in ("Wipe the counters", "Clean the oven", "Empty the pantry"):
            tasks_service.create_task(db, project_id=kitchen.id, title=title)
        kitchen_id = kitchen.id
        db.commit()
    return kitchen_id


def _assert_only_this_task(thread: Thread, task_id: int) -> None:
    """The turn created ``task_id`` and changed nothing else. These requests
    name no project, so the service's own "General" may appear with it; every
    row written is the conductor's."""
    step, before = thread.step, thread.before
    _assert_task_changes(
        before, step.world, {task_id: step.world.tasks[task_id]}, default_project_ok=True
    )
    general = {pid for pid in step.world.projects if pid not in before.projects}
    assert step.events, "the step wrote no activity events"
    assert all(
        (e[0] == "task" and e[1] == task_id) or (e[0] == "project" and e[1] in general)
        for e in step.events
    ), list(step.events)
    assert all(e[3] == CONDUCTOR for e in step.events), list(step.events)


def _assert_created_record(thread: Thread, task_id: int) -> None:
    """The typed record names the one task the turn created, with its undo."""
    step = thread.step
    title = step.world.tasks[task_id].title
    [entry] = [e for e in step.typed_record if e["tool"] == "create_task" and e["outcome"] == "done"]
    assert entry["touched"] == [
        {"app": "tasks", "kind": "task", "id": str(task_id), "title": title}
    ], entry
    assert entry["undo"]["handle"] == f"trash_task:{task_id}", entry
    assert all(e["outcome"] != "done" or e["tool"] == "create_task" for e in step.typed_record), (
        step.typed_record
    )


def _ask_to_create(sample: Sample, request: str) -> tuple[Thread, int]:
    """Send one compiled request as the conductor and return the thread (one
    step) and the id of the one task it created."""
    _seed_fleet_world(sample)
    created: list[int] = []

    def check(thread: Thread) -> None:
        _assert_completed(thread)
        assert thread.step.actor == CONDUCTOR, thread.step.actor
        assert _successful(thread.step.result, "create_task"), "expected a successful create_task"
        task_id = _new_task(thread)
        _assert_only_this_task(thread, task_id)
        _assert_created_record(thread, task_id)
        created.append(task_id)

    thread = sample.converse([(request, check)], headers=HEADERS)
    return thread, created[0]


# --- the scenarios -----------------------------------------------------------------


def _conductor_s2(sample: Sample) -> None:
    """S2: the person's words pass through; a new task with the right title
    and tomorrow's date."""
    thread, task_id = _ask_to_create(sample, S2_REQUEST)
    task = thread.step.world.tasks[task_id]
    title = task.title.lower()
    assert "water" in title and "plant" in title, f"title {task.title!r}"
    tomorrow = date.today() + timedelta(days=1)
    assert task.due_date == tomorrow, f"due {task.due_date}, expected {tomorrow}"


def _sorting_a_quoted_album(sample: Sample, request: str) -> None:
    thread, task_id = _ask_to_create(sample, request)
    task = thread.step.world.tasks[task_id]
    assert "honey" in task.title.lower(), f"title {task.title!r} dropped the quoted title"
    assert "sort" in task.title.lower(), f"title {task.title!r}"
    weekend = weekend_targets(date.today())
    assert task.due_date in weekend, f"due {task.due_date}, not this weekend {sorted(weekend)}"


def _conductor_s3_quoted_title(sample: Sample) -> None:
    """S3's tasks step: ``{s1}`` filled with an album title from another app,
    quoted. The task is named for it and due this weekend."""
    _sorting_a_quoted_album(sample, S3_REQUEST)


def _conductor_s3_quoted_title_album(sample: Sample) -> None:
    """S3's tasks step, in the wording with "the album" before the title."""
    _sorting_a_quoted_album(sample, S3_ALBUM_REQUEST)


def _conductor_s15_other_album(sample: Sample) -> None:
    """S15's second turn: a request that names no quoted title. A task about
    the Caribou album, due this weekend (conductor wants "Suddenly" in it; PCC
    has nothing to resolve that against, so it is not asked of PCC)."""
    thread, task_id = _ask_to_create(sample, S15_REQUEST)
    task = thread.step.world.tasks[task_id]
    assert "caribou" in task.title.lower(), f"title {task.title!r}"
    weekend = weekend_targets(date.today())
    assert task.due_date in weekend, f"due {task.due_date}, not this weekend {sorted(weekend)}"


def _conductor_s9_wipe_project(sample: Sample) -> None:
    """S9: "wipe the kitchen project" asks first and writes nothing; "yes"
    trashes Kitchen and its tasks, the record naming the project and its undo.
    The fleet's own confirm step redeems a token with no model turn; this is
    the model's half, as the gate's ``delegate_confirm_trash`` is."""
    kitchen_id = _seed_fleet_world(sample)

    def check_asked(thread: Thread) -> None:
        step = thread.step
        assert step.actor == CONDUCTOR, step.actor
        assert step.result.stop_reason == "confirm_required", (
            f"stopped: {step.result.stop_reason}"
        )
        assert step.reply == 'Trash project "Kitchen" and its 3 tasks?', step.reply
        assert step.events == (), f"wrote {list(step.events)}"
        assert step.world.projects[kitchen_id][1] is False, "Kitchen was trashed unasked"
        assert all(e["outcome"] != "done" for e in step.typed_record), step.typed_record

    def check_trashed(thread: Thread) -> None:
        _assert_completed(thread)
        step = thread.step
        assert _successful(step.result, "trash_project"), "expected a successful trash_project"
        assert step.world.projects[kitchen_id][1] is True, "Kitchen is still live"
        kitchen_tasks = [t for t in step.world.tasks.values() if t.project_id == kitchen_id]
        assert kitchen_tasks and all(t.deleted for t in kitchen_tasks)
        others = {pid: row for pid, row in step.world.projects.items() if pid != kitchen_id}
        assert others == {
            pid: row for pid, row in thread.before.projects.items() if pid != kitchen_id
        }, "another project was touched"
        assert all(e[3] == CONDUCTOR for e in step.events), list(step.events)
        [entry] = [
            e for e in step.typed_record if e["tool"] == "trash_project" and e["outcome"] == "done"
        ]
        assert {"app": "tasks", "kind": "project", "id": str(kitchen_id)}.items() <= (
            entry["touched"][0].items()
        ), entry
        assert entry["undo"]["handle"] == f"restore_project:{kitchen_id}", entry

    sample.converse([(S9_REQUEST, check_asked), (S9_ANSWER, check_trashed)], headers=HEADERS)


def _conductor_m3_saved_fact(sample: Sample) -> None:
    """M3 (#134): the request carries a saved fact as quoted context. The task
    is about the record player, the fact is used (due the coming Sunday, or
    Sunday in the title), and the context's own framing never lands in the
    title."""
    thread, task_id = _ask_to_create(sample, M3_REQUEST)
    task = thread.step.world.tasks[task_id]
    title = task.title.lower()
    assert "record player" in title, f"title {task.title!r}"
    assert "context from conductor" not in title and "remember" not in title, (
        f"title {task.title!r} carries the context's framing"
    )
    today = date.today()
    on_sunday = (
        task.due_date is not None
        and task.due_date.weekday() == 6
        and today <= task.due_date <= today + timedelta(days=7)
    )
    assert on_sunday or "sunday" in title, (
        f"the saved fact went unused: due {task.due_date}, title {task.title!r}"
    )


def test_eval_conductor_s2_add_task() -> None:
    _gate("conductor_s2_add_task", _conductor_s2, tier=TIER)


def test_eval_conductor_s3_quoted_title() -> None:
    _gate("conductor_s3_quoted_title", _conductor_s3_quoted_title, tier=TIER)


def test_eval_conductor_s3_quoted_title_album() -> None:
    _gate("conductor_s3_quoted_title_album", _conductor_s3_quoted_title_album, tier=TIER)


def test_eval_conductor_s15_other_album() -> None:
    _gate("conductor_s15_other_album", _conductor_s15_other_album, tier=TIER)


def test_eval_conductor_s9_wipe_project() -> None:
    _gate("conductor_s9_wipe_project", _conductor_s9_wipe_project, tier=TIER)


def test_eval_conductor_m3_saved_fact() -> None:
    _gate("conductor_m3_saved_fact", _conductor_m3_saved_fact, tier=TIER)
