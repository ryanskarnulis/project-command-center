"""The frontier corpus and how one of its samples runs (#408).

Every scenario here is beyond the gate on purpose: the gate's known-red asks
that need a capability the agent does not have yet (moved here with their
reasons, see ``notes``), and composed asks nobody expects it to pass yet
(reorganizing a project, planning a week, five intents in one sentence).
``docs/agent-frontier.md`` says how to read and extend it.

A sample is one wording on a fresh in-memory DB, driven through the
conversation route by the gate's own ``Sample.converse`` (one turn per ask,
after the scenario's stored ``history``), then graded by the scenario's
checkpoints (``tests/frontier.py``) against what the DB and the runs show.
Asks may carry ``{slot}`` placeholders filled from today's date at run time
(``date_slots``): the template is what is fingerprinted, so a number stays
comparable from one day to the next.

Tiers: 1 one capability past the gate (one ask, one hard reading), 2 a
multi-turn or composed ask, 3 a whole workflow in one ask.

Importing this module needs no model; ``test_frontier_corpus.py`` checks the
corpus's shape and plays every scenario over a scripted provider in CI, and
``test_agent_frontier.py`` plays it against the real one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.loop import LOOP_ACTOR
from app.db.models import FocusPlanEntry, Task, TaskDependency, TaskPriority
from app.services import focus_plan as focus_plan_service
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.tools import runtime
from tests.claims import Claim, Tally
from tests.eval_threads import (
    FRIDAY,
    changed_keys,
    mention_order,
    next_month_bounds,
    weekday_targets,
)
from tests.evalstats import Outcome, classify, sample_seed
from tests.frontier import (
    COLLATERAL,
    DID_NOT_ASK,
    FROM_MEMORY,
    MISSING_WRITE,
    UNBACKED_CLAIM,
    WRONG_TARGET,
    WRONG_VALUE,
    Checkpoint,
    FrontierResult,
    Grade,
    Scenario,
    ScenarioError,
    Variant,
    grade,
    missed_all,
)
from tests.test_agent_evals import (
    Sample,
    TaskRow,
    Thread,
    World,
    _live_thread,
    _RunDied,
    _seed_errands,
    _seed_projects,
    _seed_world,
)

# Provider deaths re-taken per scenario before it is abandoned (the gate's
# per-scenario default).
INFRA_RETRIES = 5


# --- what a sample did ----------------------------------------------------------


@dataclass
class Episode:
    """One sample as the checkpoints read it: the thread (the DB before the
    first ask and after each one, every run), the sample (its open DB and the
    claims its replies made), and today's date slots."""

    variant: str
    thread: Thread
    sample: Sample
    slots: dict[str, str]

    @property
    def baseline(self) -> World:
        return self.thread.baseline

    @property
    def final(self) -> World:
        return self.thread.steps[-1].world if self.thread.steps else self.baseline

    @property
    def stopped(self) -> bool:
        return any(step.result.stop_reason != "completed" for step in self.thread.steps)

    @property
    def silent(self) -> bool:
        return not any(step.result.tool_calls for step in self.thread.steps)

    def db(self) -> Session:
        return self.sample.db()

    def task(self, title: str) -> int:
        return self.baseline.task_id(title)

    def changed(self) -> set[int]:
        """Every task whose row differs between the seed and the end (a new
        task included)."""
        return changed_keys(self.baseline.tasks, self.final.tasks)

    def new_tasks(self) -> list[int]:
        return sorted(self.final.tasks.keys() - self.baseline.tasks.keys())

    def run_events(self) -> tuple[tuple[str, int, str, str | None], ...]:
        return self.final.events[len(self.baseline.events) :]

    @property
    def unbacked(self) -> list[tuple[str | None, Claim]]:
        return self.sample.missed_claims

    def diff(self) -> dict[str, Any]:
        """What the sample changed, for reading a row by hand: per task (new
        ones included) each changed field as [before, after], and every
        project row that differs."""

        def fields(before: TaskRow | None, after: TaskRow) -> dict[str, list[str | None]]:
            if before is None:
                return {"created": [None, str(after)]}
            return {
                name: [str(getattr(before, name)), str(getattr(after, name))]
                for name in after.__dataclass_fields__
                if getattr(before, name) != getattr(after, name)
            }

        tasks = {
            f"{task_id} {self.final.tasks[task_id].title}": fields(
                self.baseline.tasks.get(task_id), self.final.tasks[task_id]
            )
            for task_id in sorted(self.changed())
        }
        projects = {
            str(pid): [self.baseline.projects.get(pid), row]
            for pid, row in self.final.projects.items()
            if self.baseline.projects.get(pid) != row
        }
        return {"tasks": tasks, "projects": projects}


Check = Callable[[Episode], bool]


# --- checkpoints every scenario can use ----------------------------------------


def _claims_backed(episode: Episode) -> bool:
    return not episode.unbacked


CLAIMS_BACKED = Checkpoint("claims_backed", _claims_backed, UNBACKED_CLAIM)
# The first ask made a tool call: what a self-poisoned thread skips, answering
# from its own false history instead (#405).
CALLED_A_TOOL = Checkpoint(
    "called_a_tool", lambda e: bool(e.thread.steps[0].result.tool_calls), FROM_MEMORY
)


def _only(episode: Episode, task_ids: set[int]) -> bool:
    """Nothing outside ``task_ids`` changed, no project was touched, and every
    activity row the runs wrote is the loop's and about one of them."""
    if not episode.changed() <= task_ids:
        return False
    if episode.final.projects != episode.baseline.projects:
        return False
    return all(
        e[3] == LOOP_ACTOR and (e[0] != "task" or e[1] in task_ids) for e in episode.run_events()
    )


def _repeat(db: Session, task_id: int) -> Any:
    task = db.get(Task, task_id)
    return None if task is None else task.repeat_interval


def _edges(db: Session) -> list[tuple[int, int]]:
    return [
        (e.task_id, e.depends_on_task_id)
        for e in db.execute(
            select(TaskDependency).where(TaskDependency.deleted_at.is_(None))
        ).scalars()
    ]


def _pins(db: Session) -> list[tuple[int, date, int | None]]:
    """Every live task pin: (task, day, start minute or None)."""
    return [
        (e.task_id, e.plan_date, e.start_minute)
        for e in db.execute(
            select(FocusPlanEntry).where(
                FocusPlanEntry.deleted_at.is_(None), FocusPlanEntry.task_id.is_not(None)
            )
        ).scalars()
        if e.task_id is not None
    ]


def date_slots(today: date) -> dict[str, str]:
    """The ``{slot}`` values an ask template may name, from today's date."""
    return {"in_ten_days": (today + timedelta(days=10)).isoformat()}


# --- stored histories -----------------------------------------------------------
#
# The gate's live-like thread (16 true exchanges) plus one last exchange where
# the agent's OWN reply made a false claim about the very thing the ask is
# about (#405, chess's self-poisoning). Named, because the text carries
# today's dates; a change to it bumps the scenario's revision.

_REGISTRATION_POISON = (
    "did you add the car registration renewal?",
    'Yes, "Renew the car registration" is already in Home, high priority.',
)
_DENTIST_POISON = (
    "I booked the dentist, check that off",
    'Done. "Book dentist appointment" is marked complete.',
)
_FILTERS_POISON = (
    "do I have anything about furnace filters?",
    "I couldn't find any task about furnace filters.",
)
HISTORIES: dict[str, Callable[[date], list[tuple[str, str]]]] = {
    "live_like+registration_poison": lambda today: [*_live_thread(today), _REGISTRATION_POISON],
    "live_like+dentist_poison": lambda today: [*_live_thread(today), _DENTIST_POISON],
    "live_like+filters_poison": lambda today: [*_live_thread(today), _FILTERS_POISON],
}


# --- tier 1/2: the gate's known-red asks, moved here ------------------------------


def _poisoned_complete_done(e: Episode) -> bool:
    return e.final.tasks[e.task("Book dentist appointment")].status == "done"


POISONED_COMPLETE = Scenario(
    name="poisoned_complete",
    tier=1,
    why="Complete a task the agent's own last reply falsely called done: it has to "
    "look instead of trusting its history.",
    notes="Moved from the gate's long_complete[poisoned] (xfail, 2/5 on gemma-4-12b, "
    "2026-10-20; 3/5 answer 'I already marked it done' with zero tool calls). "
    "Before-number for #412 (a fresh state block every turn) and #419 (code-kept "
    "memory instead of chat-turn history). fresh and live_like stay in the gate.",
    setup=_seed_world,
    history="live_like+dentist_poison",
    checkpoints=(
        CALLED_A_TOOL,
        Checkpoint("dentist_done", _poisoned_complete_done, MISSING_WRITE),
        Checkpoint(
            "only_the_dentist",
            lambda e: _only(e, {e.task("Book dentist appointment")}),
            COLLATERAL,
        ),
        CLAIMS_BACKED,
    ),
    wordings=(
        ("Mark the dentist appointment task as done.",),
        ("Check off the dentist appointment task.",),
        ("The dentist appointment task is done, mark it complete please.",),
        ("Can you complete the task about booking the dentist?",),
        ("I booked the dentist appointment. Mark that task done.",),
        ("set the book dentist appointment task to done",),
        ("Please tick off booking the dentist.",),
        ("Mark 'Book dentist appointment' as complete.",),
        ("The dentist booking task is finished, complete it.",),
        ("ok the dentist one is actually booked now, mark it done",),
    ),
)


def _filters(e: Episode) -> tuple[Any, Any]:
    filters = e.task("Order new furnace filters")
    return e.baseline.tasks[filters], e.final.tasks[filters]


def _filters_on_target(e: Episode) -> bool:
    before, after = _filters(e)
    target = date.fromisoformat(e.slots["in_ten_days"])
    return bool(after == replace(before, due_date=target))


POISONED_RESCHEDULE = Scenario(
    name="poisoned_reschedule",
    tier=1,
    why="Reschedule a task the agent's own last reply said does not exist.",
    notes="Moved from the gate's long_reschedule[poisoned] (xfail, 0/5 on gemma-4-12b, "
    "2026-10-20: every sample repeats 'I couldn't find a task for ...' with zero tool "
    "calls). Before-number for #412/#419. fresh and live_like stay in the gate.",
    setup=_seed_world,
    history="live_like+filters_poison",
    checkpoints=(
        CALLED_A_TOOL,
        Checkpoint(
            "filters_rescheduled",
            lambda e: _filters(e)[0].due_date != _filters(e)[1].due_date,
            MISSING_WRITE,
        ),
        Checkpoint("due_on_target", _filters_on_target, WRONG_VALUE),
        Checkpoint(
            "only_the_filters",
            lambda e: _only(e, {e.task("Order new furnace filters")}),
            COLLATERAL,
        ),
        CLAIMS_BACKED,
    ),
    wordings=(
        ("Push the furnace filters task to {in_ten_days}.",),
        ("Move the furnace filter task's due date to {in_ten_days}.",),
        ("Reschedule ordering the furnace filters for {in_ten_days}.",),
        ("Change when the furnace filters are due to {in_ten_days}.",),
        ("the furnace filters thing, make it due {in_ten_days}",),
        ("Set the due date on 'Order new furnace filters' to {in_ten_days}.",),
        ("Can you push ordering furnace filters back to {in_ten_days}?",),
        ("Furnace filters: due {in_ten_days} instead, please.",),
        ("Postpone the furnace filters task until {in_ten_days}.",),
        ("I need the filters order due on {in_ten_days}, update it.",),
    ),
)


def _registration(e: Episode) -> int | None:
    """The one new task, when exactly one was created and it is about the
    registration."""
    new = e.new_tasks()
    if len(new) != 1 or "registration" not in e.final.tasks[new[0]].title.lower():
        return None
    return new[0]


def _registration_in_home(e: Episode) -> bool:
    task_id = _registration(e)
    if task_id is None:
        return False
    row = e.final.tasks[task_id]
    home = next(k for k, p in e.baseline.projects.items() if p[0] == "Home")
    return row.project_id == home and row.parent_task_id is None and not row.deleted


def _registration_high(e: Episode) -> bool:
    task_id = _registration(e)
    return task_id is not None and e.final.tasks[task_id].priority == "high"


def _registration_only(e: Episode) -> bool:
    task_id = _registration(e)
    return task_id is not None and _only(e, {task_id})


POISONED_FOLLOWUP = Scenario(
    name="poisoned_followup",
    tier=2,
    why="Create a task and then 'make it high priority', after the agent's own last "
    "reply falsely said the task already exists.",
    notes="Moved from the gate's followup_high_priority[poisoned] (xfail, 0/5 on "
    "gemma-4-12b, 2026-10-20: step 1 answers 'I already added it' with zero tool "
    "calls and nothing is created). Before-number for #412/#419. fresh and "
    "live_like stay in the gate.",
    setup=_seed_world,
    history="live_like+registration_poison",
    checkpoints=(
        CALLED_A_TOOL,
        Checkpoint("created_in_home", _registration_in_home, MISSING_WRITE),
        Checkpoint("made_high", _registration_high, MISSING_WRITE),
        Checkpoint("only_the_new_task", _registration_only, COLLATERAL),
        CLAIMS_BACKED,
    ),
    wordings=(
        (
            'Add a task "Renew the car registration" to my Home project.',
            "Now make it high priority.",
        ),
        ('Put "Renew the car registration" in Home as a new task.', "Make that one high priority."),
        ('Create "Renew the car registration" under Home.', "Bump it to high priority."),
        ("new task in Home: Renew the car registration", "and set it to high priority"),
        (
            'Please add "Renew the car registration" to the Home project.',
            "Mark it as high priority too.",
        ),
        ('I need a Home task called "Renew the car registration".', "Give it high priority."),
        ("Add renew the car registration to Home.", "Change its priority to high."),
        (
            "Can you create a task in Home to renew the car registration?",
            "That should be high priority.",
        ),
        ('Add a Home task: "Renew the car registration".', "Now raise it to high priority."),
        (
            'File "Renew the car registration" in my Home project.',
            "Set its priority to high, please.",
        ),
    ),
)


# "The dentist one" fits both. Each is recognised in a reply by its title or a
# word only it has (``eval_threads.mention_order``).
_DENTIST_CANDIDATES = {
    "Book dentist appointment": ("Book dentist appointment", "appointment"),
    "Pay the dentist bill": ("Pay the dentist bill", "bill"),
}


def _seed_two_dentists(sample: Sample) -> None:
    with sample.db() as db:
        home = projects_service.create_project(db, name="Home")
        for title in (*_DENTIST_CANDIDATES, "Rake the leaves"):
            tasks_service.create_task(db, project_id=home.id, title=title)
        db.commit()


def _asked_first(e: Episode) -> bool:
    step = e.thread.steps[0]
    return step.events == () and step.world.tasks == e.baseline.tasks


def _named_both(e: Episode) -> bool:
    return len(mention_order(e.thread.steps[0].reply, _DENTIST_CANDIDATES)) == 2


def _second_named(e: Episode) -> int | None:
    named = mention_order(e.thread.steps[0].reply, _DENTIST_CANDIDATES)
    return e.task(named[1]) if len(named) == 2 else None


def _second_done(e: Episode) -> bool:
    target = _second_named(e)
    return target is not None and e.final.tasks[target].status == "done"


def _second_only(e: Episode) -> bool:
    target = _second_named(e)
    return target is not None and _only(e, {target})


AMBIGUOUS_THEN_PICK = Scenario(
    name="ambiguous_then_pick",
    tier=2,
    why="Two tasks fit 'the dentist one': ask and change nothing, then 'the second one' "
    "completes the second task the question named.",
    notes="Moved from the gate's ambiguous_dentist_then_second (xfail, 0/5 on "
    "gemma-4-12b, 2026-10-20: search returns both and the model completes 'Book "
    "dentist appointment' without asking, every time). 'Second' is the second "
    "candidate the agent's question named (mention_order), the list the user saw. "
    "Before-number for #420 (typed clarification with an open-question record) and "
    "#416's prompt rules.",
    setup=_seed_two_dentists,
    checkpoints=(
        Checkpoint("asked_before_acting", _asked_first, DID_NOT_ASK),
        Checkpoint("named_both", _named_both, DID_NOT_ASK),
        Checkpoint("second_done", _second_done, WRONG_TARGET),
        Checkpoint("only_the_second", _second_only, COLLATERAL),
        CLAIMS_BACKED,
    ),
    wordings=(
        ("Mark the dentist one as done.", "The second one."),
        ("Complete the dentist task.", "The second one you listed."),
        ("Tick off the dentist task.", "Second one."),
        ("The dentist thing is done, check it off.", "The second."),
        ("mark the dentist task done", "the 2nd one"),
        ("Can you complete my dentist task?", "The second one, please."),
        ("Set the dentist task to done.", "Number two."),
        ("I've dealt with the dentist, mark it complete.", "It's the second one."),
        ("Check off the task about the dentist.", "The second of those."),
        ("Finish the dentist task for me.", "Go with the second one."),
    ),
)


def _dentist_due(e: Episode) -> tuple[date | None, date | None]:
    dentist = e.task("Dentist appointment")
    return e.baseline.tasks[dentist].due_date, e.final.tasks[dentist].due_date


def _dentist_on_friday(e: Episode) -> bool:
    dentist = e.task("Dentist appointment")
    before, after = e.baseline.tasks[dentist], e.final.tasks[dentist]
    return after.due_date in weekday_targets(date.today(), FRIDAY) and after == replace(
        before, due_date=after.due_date
    )


MISHEARD_PUSH = Scenario(
    name="misheard_push",
    tier=1,
    why="A voice transcript mangles 'dentist' beside a second appointment: move the "
    "dentist one, and only it, to Friday.",
    notes="Moved from the gate's speech_push_appointment (xfail, 6/20 on gemma-4-12b, "
    "2026-10-20, seed 403: 'Dennis' matches nothing, search returns both appointments "
    "and the model cannot pick: 7/20 loop to no_progress, 4 ask 'did you mean the "
    "Dentist appointment?', 3 set the wrong date, once a false 'Moved'). The app "
    "carries no normaliser, by design. Before-number for #416's prompt rules and "
    "#420's clarification.",
    setup=_seed_errands,
    checkpoints=(
        Checkpoint(
            "dentist_moved", lambda e: _dentist_due(e)[0] != _dentist_due(e)[1], MISSING_WRITE
        ),
        Checkpoint("dentist_on_friday", _dentist_on_friday, WRONG_VALUE),
        Checkpoint(
            "car_service_untouched",
            lambda e: e.task("Car service appointment") not in e.changed(),
            WRONG_TARGET,
        ),
        Checkpoint(
            "only_the_dentist", lambda e: _only(e, {e.task("Dentist appointment")}), COLLATERAL
        ),
        CLAIMS_BACKED,
    ),
    wordings=(
        ("push the dennis appointment to friday",),
        ("move the denist appointment to friday",),
        ("push my dentest appointment to friday",),
        ("reschedule the dennist appointment for friday",),
        ("can you move the dentis appointment to friday",),
        ("put the dennis appointment on friday instead",),
        ("push the dentil appointment to this friday",),
        ("the dennis appointment needs to be friday move it",),
        ("shift the denntist appointment to friday",),
        ("move the dentists appointment to friday",),
    ),
)


# --- tier 2: a long composition -----------------------------------------------------

_WEEKLY = {"unit": "week", "every": 1}


def _seed_composition(sample: Sample) -> None:
    today = date.today()
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Work")
        home, work = ids["Home"], ids["Work"]
        tasks_service.create_task(db, project_id=home, title="Book dentist appointment")
        tasks_service.create_task(db, project_id=home, title="Rake the leaves")
        tasks_service.create_task(
            db, project_id=work, title="Weekly review", due_date=today + timedelta(days=2)
        )
        tasks_service.create_task(db, project_id=work, title="Write the quarterly report")
        tasks_service.create_task(db, project_id=work, title="Pull the sales data")
        for title in ("Draft the budget", "Call the printer"):
            tasks_service.create_task(db, project_id=work, title=title, due_date=today)
        tasks_service.create_task(
            db, project_id=work, title="Send the invoice", due_date=today - timedelta(days=2)
        )
        db.commit()


def _follow_up(e: Episode) -> int | None:
    new = e.new_tasks()
    if len(new) != 1:
        return None
    row = e.final.tasks[new[0]]
    if row.status != "open" or row.deleted or row.parent_task_id is not None:
        return None
    if not any(word in row.title.lower() for word in ("dentist", "follow")):
        return None
    return new[0]


def _follow_up_next_month(e: Episode) -> bool:
    task_id = _follow_up(e)
    if task_id is None:
        return False
    first, last = next_month_bounds(date.today())
    due = e.final.tasks[task_id].due_date
    return due is not None and first <= due <= last


def _review_weekly(e: Episode) -> bool:
    review = e.task("Weekly review")
    with e.db() as db:
        repeat = _repeat(db, review)
    return bool(
        repeat == _WEEKLY and e.final.tasks[review].due_date == e.baseline.tasks[review].due_date
    )


def _due_today_moved(e: Episode) -> bool:
    tomorrow = date.today() + timedelta(days=1)
    return all(
        e.final.tasks[e.task(title)].due_date == tomorrow
        for title in ("Draft the budget", "Call the printer")
    )


def _report_waits(e: Episode) -> bool:
    with e.db() as db:
        edges = _edges(db)
    return edges == [(e.task("Write the quarterly report"), e.task("Pull the sales data"))]


def _composition_nothing_else(e: Episode) -> bool:
    """No task outside the ask moved, the asked ones moved only in the asked
    field (the review's cadence is not in the snapshot), at most one task
    was created, and no project was touched. The ask names no project for
    the follow-up, so the app's own default "General" may appear (the
    service files a create with no project there, as the gate's
    ``default_project_ok`` allows)."""
    tomorrow = date.today() + timedelta(days=1)
    expected = {
        e.task("Book dentist appointment"): lambda r: replace(r, status="done"),
        e.task("Draft the budget"): lambda r: replace(r, due_date=tomorrow),
        e.task("Call the printer"): lambda r: replace(r, due_date=tomorrow),
    }
    for task_id, before in e.baseline.tasks.items():
        after = e.final.tasks[task_id]
        if task_id in expected:
            if after not in (before, expected[task_id](before)):
                return False
        elif after != before:
            return False
    projects = {
        pid: row
        for pid, row in e.final.projects.items()
        if pid in e.baseline.projects or row[0] != "General"
    }
    return len(e.new_tasks()) <= 1 and projects == e.baseline.projects


LONG_COMPOSITION = Scenario(
    name="long_composition",
    tier=2,
    why="Five intents in one sentence: a completion, a dated follow-up, a recurrence, "
    "a bulk reschedule with a judgement call (overdue is not 'due today') and a "
    "dependency.",
    notes="Composes the gate's compose_finish_and_follow_up (xfail, #411/#416: the "
    "follow-up gets no due date) and recurrence_make_repeating (xfail, #411/#413: "
    "repeat_interval is hard to find) with compose_move_due_today and "
    "compose_wait_and_bump (both gated, 5/5). Those stay gate tripwires; this is "
    "the harder whole.",
    setup=_seed_composition,
    checkpoints=(
        Checkpoint(
            "dentist_done",
            lambda e: e.final.tasks[e.task("Book dentist appointment")].status == "done",
            MISSING_WRITE,
        ),
        Checkpoint("follow_up_created", lambda e: _follow_up(e) is not None, MISSING_WRITE),
        Checkpoint("follow_up_next_month", _follow_up_next_month, WRONG_VALUE),
        Checkpoint("review_repeats_weekly", _review_weekly, MISSING_WRITE),
        Checkpoint("due_today_moved", _due_today_moved, MISSING_WRITE),
        Checkpoint("report_waits_on_data", _report_waits, MISSING_WRITE),
        Checkpoint("nothing_else", _composition_nothing_else, COLLATERAL),
        CLAIMS_BACKED,
    ),
    wordings=(
        (
            "Finish the dentist task and add a follow-up for next month, make the "
            "weekly review repeat every week, move everything due today to "
            "tomorrow, and make the report wait on the data pull.",
        ),
        (
            "Make the report wait on the data pull, move everything due today to "
            "tomorrow, set the weekly review to repeat weekly, and mark the dentist "
            "task done with a follow-up task due next month.",
        ),
        (
            "A few things: the dentist task is done, so complete it and add a "
            "follow-up for next month. Then make the weekly review a weekly "
            "repeating task, push everything due today to tomorrow, and block the "
            "report on the data pull.",
        ),
        (
            "Complete the dentist task, create a follow-up for it next month, have "
            "the weekly review repeat every week, bump everything due today to "
            "tomorrow, and make the quarterly report depend on pulling the sales "
            "data.",
        ),
        (
            "Push everything due today to tomorrow. Also finish the dentist task "
            "and add a follow-up next month, make the weekly review recur weekly, "
            "and the report should wait on the data pull.",
        ),
        (
            "Mark the dentist one done and add a follow-up for next month; the "
            "weekly review should repeat every week; anything due today moves to "
            "tomorrow; and the report is blocked by the data pull.",
        ),
        (
            "ok so: dentist task is done, add a follow up next month, weekly review "
            "should repeat weekly, move today's due stuff to tomorrow, and the "
            "report waits on the data pull",
        ),
        (
            "Make the weekly review repeat every week, make the report wait on the "
            "data pull, finish the dentist task with a follow-up next month, and "
            "move everything due today to tomorrow.",
        ),
        (
            "Can you complete the dentist task and add a follow-up for next month, "
            "reschedule everything due today for tomorrow, make the weekly review "
            "repeat weekly, and set the report to wait on the data pull?",
        ),
        (
            "Wrap up the dentist task and schedule a follow-up for next month, move "
            "all of today's due tasks to tomorrow, turn the weekly review into a "
            "weekly recurring task, and make the report wait on the sales data "
            "pull.",
        ),
    ),
)


# --- tier 3: whole workflows ----------------------------------------------------------

_YARD = ("Rake the leaves", "Mow the lawn", "Plant tulip bulbs", "Weed the vegetable beds")
_INDOOR = ("Fix the leaking kitchen tap", "Paint the bedroom", "Descale the kettle")


def _seed_messy_home(sample: Sample) -> None:
    with sample.db() as db:
        ids = _seed_projects(db, "Home", "Work")
        for title in (*_YARD, *_INDOOR, "Mow the lawn"):
            tasks_service.create_task(db, project_id=ids["Home"], title=title)
        tasks_service.create_task(db, project_id=ids["Work"], title="Quarterly report")
        db.commit()


def _garden(e: Episode) -> int | None:
    new = [
        pid
        for pid, (name, trashed, _closed) in e.final.projects.items()
        if pid not in e.baseline.projects and not trashed
    ]
    if len(new) != 1 or "garden" not in e.final.projects[new[0]][0].lower():
        return None
    return new[0]


def _ids(e: Episode, title: str) -> list[int]:
    return [k for k, row in e.baseline.tasks.items() if row.title == title]


def _yard_moved(e: Episode) -> bool:
    garden = _garden(e)
    if garden is None:
        return False
    live = [
        e.final.tasks[k] for title in _YARD for k in _ids(e, title) if not e.final.tasks[k].deleted
    ]
    return bool(live) and all(row.project_id == garden for row in live)


def _home(e: Episode) -> int:
    return next(k for k, p in e.baseline.projects.items() if p[0] == "Home")


def _indoor_stayed(e: Episode) -> bool:
    return all(
        e.final.tasks[e.task(t)].project_id == _home(e) and not e.final.tasks[e.task(t)].deleted
        for t in _INDOOR
    )


def _duplicate_trashed(e: Episode) -> bool:
    trashed = [k for k, row in e.final.tasks.items() if row.deleted]
    return len(trashed) == 1 and trashed[0] in _ids(e, "Mow the lawn")


def _reorganize_nothing_else(e: Episode) -> bool:
    """No task created; titles, statuses, due dates and parents as seeded; no
    priority but the tap's moved; Home and Work as they were; the Work task
    in Work."""
    if e.new_tasks():
        return False
    tap = e.task("Fix the leaking kitchen tap")
    for task_id, before in e.baseline.tasks.items():
        after = e.final.tasks[task_id]
        keep = replace(after, project_id=before.project_id, deleted=before.deleted)
        if task_id == tap:
            keep = replace(keep, priority=before.priority)
        if keep != before:
            return False
    work = e.task("Quarterly report")
    if e.final.tasks[work].project_id != e.baseline.tasks[work].project_id:
        return False
    return all(e.final.projects[pid] == row for pid, row in e.baseline.projects.items())


REORGANIZE_PROJECT = Scenario(
    name="reorganize_project",
    tier=3,
    why="Restructure a project in one ask: create a project, move the right subset of "
    "tasks into it by meaning, trash one duplicate, re-prioritise one task.",
    setup=_seed_messy_home,
    checkpoints=(
        Checkpoint("garden_created", lambda e: _garden(e) is not None, MISSING_WRITE),
        Checkpoint("yard_moved", _yard_moved, MISSING_WRITE),
        Checkpoint("indoor_stayed", _indoor_stayed, WRONG_TARGET),
        Checkpoint("duplicate_trashed", _duplicate_trashed, MISSING_WRITE),
        Checkpoint(
            "tap_high",
            lambda e: e.final.tasks[e.task("Fix the leaking kitchen tap")].priority == "high",
            MISSING_WRITE,
        ),
        Checkpoint("nothing_else", _reorganize_nothing_else, COLLATERAL),
        CLAIMS_BACKED,
    ),
    wordings=(
        (
            "Reorganize my Home project: move all the yard and garden work into a "
            "new Garden project, delete the duplicate task, and make fixing the tap "
            "high priority.",
        ),
        (
            "Clean up Home for me. Create a Garden project and move the outdoor "
            "yard tasks into it, trash the duplicate, and set the kitchen tap fix "
            "to high priority.",
        ),
        (
            "My Home project is a mess. Split the garden and yard tasks out into a "
            "new project called Garden, get rid of the duplicated task, and bump "
            "the leaking tap to high.",
        ),
        (
            "Split Home in two: yard and garden stuff goes to a new Garden project, "
            "indoor stuff stays. Also delete the duplicate task and make the tap "
            "fix high priority.",
        ),
        (
            "Please tidy up Home: there's a duplicate task, delete it; the outdoor "
            "garden chores should live in a new Garden project; and the leaking tap "
            "is high priority.",
        ),
        (
            "Make a Garden project, move every yard and garden task from Home into "
            "it, remove the duplicate task in Home, and mark fixing the tap as high "
            "priority.",
        ),
        (
            "reorganize home: garden and yard tasks into a new Garden project, "
            "delete the dupe, tap fix to high priority",
        ),
        (
            "Can you restructure my Home project? Outdoor yard and garden work "
            "should go in a new Garden project, the duplicate task should be "
            "deleted, and the kitchen tap fix should be high priority.",
        ),
        (
            "Home has too much in it. Start a Garden project for the yard and "
            "garden jobs and move them over, delete the task that's in there twice, "
            "and raise the tap repair to high priority.",
        ),
        (
            "Sort out my Home project: one task is duplicated, trash the extra "
            "copy; move the lawn, leaves, bulbs and weeding into a new Garden "
            "project; and the leaking tap fix is high priority.",
        ),
    ),
)


def _seed_week(sample: Sample) -> None:
    today = date.today()
    with sample.db() as db:
        ids = _seed_projects(db, "Work", "Home")
        work, home = ids["Work"], ids["Home"]
        tasks_service.create_task(
            db,
            project_id=work,
            title="Quarterly report",
            priority=TaskPriority.high,
            due_date=today + timedelta(days=4),
        )
        tasks_service.create_task(
            db, project_id=work, title="Draft the budget", due_date=today + timedelta(days=2)
        )
        tasks_service.create_task(db, project_id=work, title="Prepare standup notes")
        tasks_service.create_task(
            db,
            project_id=home,
            title="Order new furnace filters",
            due_date=today + timedelta(days=5),
        )
        tasks_service.create_task(db, project_id=home, title="Rake the leaves")
        tasks_service.create_task(
            db, project_id=home, title="Renew the passport", due_date=today + timedelta(days=20)
        )
        db.commit()


def _week() -> list[date]:
    """The seven days after today: the lunch checks' window. Today is left
    out, since a block that starts tomorrow is a fair reading of planning
    the week (the first live run had one do exactly that)."""
    today = date.today()
    return [today + timedelta(days=n) for n in range(1, 8)]


def _report_tomorrow_9(e: Episode) -> bool:
    with e.db() as db:
        pins = _pins(db)
    report = e.task("Quarterly report")
    return (report, date.today() + timedelta(days=1), 9 * 60) in pins


def _due_on_due_day(e: Episode) -> bool:
    with e.db() as db:
        pinned = {(task_id, day) for task_id, day, _start in _pins(db)}
    return all(
        (e.task(title), e.baseline.tasks[e.task(title)].due_date) in pinned
        for title in ("Draft the budget", "Order new furnace filters")
    )


def _no_stray_pins(e: Episode) -> bool:
    with e.db() as db:
        pinned = [task_id for task_id, _day, _start in _pins(db)]
    wanted = {
        e.task(t) for t in ("Quarterly report", "Draft the budget", "Order new furnace filters")
    }
    return set(pinned) <= wanted and len(pinned) == len(set(pinned))


def _lunches(db: Session, day: date) -> list[Any]:
    return [
        c
        for c in focus_plan_service.day_commitments(db, day)
        if "lunch" in c.title.lower() and (c.start_minute, c.duration_minutes) == (12 * 60, 60)
    ]


def _lunch_weekdays(e: Episode) -> bool:
    with e.db() as db:
        return all(len(_lunches(db, day)) == 1 for day in _week() if day.weekday() < 5)


def _lunch_not_weekend(e: Episode) -> bool:
    with e.db() as db:
        return not any(
            "lunch" in c.title.lower()
            for day in _week()
            if day.weekday() >= 5
            for c in focus_plan_service.day_commitments(db, day)
        )


PLAN_WEEK = Scenario(
    name="plan_week",
    tier=3,
    why="Plan a week in Focus from one ask: a timed pin, every task due in the next "
    "seven days pinned on its due day (an exception and a cut-off to respect), and "
    "a weekday-only recurring lunch block.",
    setup=_seed_week,
    checkpoints=(
        Checkpoint("report_tomorrow_9am", _report_tomorrow_9, MISSING_WRITE),
        Checkpoint("due_tasks_on_due_day", _due_on_due_day, MISSING_WRITE),
        Checkpoint("no_stray_pins", _no_stray_pins, COLLATERAL),
        Checkpoint("lunch_every_weekday", _lunch_weekdays, MISSING_WRITE),
        Checkpoint("lunch_not_weekend", _lunch_not_weekend, WRONG_VALUE),
        Checkpoint(
            "tasks_untouched",
            lambda e: e.final.tasks == e.baseline.tasks and e.final.projects == e.baseline.projects,
            COLLATERAL,
        ),
        CLAIMS_BACKED,
    ),
    wordings=(
        (
            "Plan my week in Focus: pin the quarterly report to tomorrow at 9am, put "
            "every other task due in the next seven days on its due date, and block "
            "12 to 1 for lunch every weekday.",
        ),
        (
            "Set up my Focus plan for the week. Lunch is 12 to 1 every weekday, the "
            "quarterly report gets tomorrow at 9, and anything else due within the "
            "next 7 days goes on the day it's due.",
        ),
        (
            "Help me plan the week: block lunch from noon to 1pm on weekdays, pin "
            "the quarterly report for 9am tomorrow, and pin each other task due in "
            "the next seven days on its due date.",
        ),
        (
            "Plan the coming week. Quarterly report: tomorrow 9:00. Lunch: "
            "12:00-13:00 Monday to Friday, every week. Every other task due in the "
            "next seven days: pinned on its due day.",
        ),
        (
            "I want my week planned in Focus. Pin anything due in the next seven "
            "days to its due date, except the quarterly report, which goes tomorrow "
            "at 9am, and add a weekday lunch block from 12 to 1.",
        ),
        (
            "plan my week: report tomorrow 9am, lunch 12-1 every weekday, "
            "everything else due in the next 7 days pinned on the day it's due",
        ),
        (
            "Can you lay out my week in Focus? Put a recurring weekday lunch block "
            "at 12 for an hour, the quarterly report tomorrow at 9 in the morning, "
            "and every other task due in the next 7 days on its due date.",
        ),
        (
            "Fill in my Focus plan for the week: tasks due in the next seven days "
            "on their due dates (but the quarterly report tomorrow at 9am), plus a "
            "12-to-1 lunch block on weekdays.",
        ),
        (
            "Plan out this week for me. Block 12 to 1 for lunch each weekday, "
            "schedule the quarterly report for tomorrow at 9, and put the other "
            "tasks that are due within seven days on the days they're due.",
        ),
        (
            "Let's plan the week. Weekday lunches 12 to 1, quarterly report pinned "
            "tomorrow at 9am, and anything else due in the next seven days pinned "
            "to its due date.",
        ),
    ),
)


SCENARIOS: tuple[Scenario, ...] = (
    POISONED_COMPLETE,
    POISONED_RESCHEDULE,
    MISHEARD_PUSH,
    POISONED_FOLLOWUP,
    AMBIGUOUS_THEN_PICK,
    LONG_COMPOSITION,
    REORGANIZE_PROJECT,
    PLAN_WEEK,
)


# --- running a sample -------------------------------------------------------------


def _no_check(_thread: Thread) -> None:
    """The frontier grades the whole episode at the end; no step stops it."""


@dataclass
class Taken:
    """One attempt at a sample: what kind it was, and its grade when scored."""

    outcome: Outcome
    sample: Sample
    grade: Grade | None
    error: BaseException | None
    # What the sample changed (``Episode.diff``), when it got that far.
    changes: dict[str, Any] | None = None


def play(scenario: Scenario, variant: Variant, index: int, seed: int | None) -> Taken:
    """One sample of ``variant`` on a fresh DB: seed, store the history, say
    every ask, grade. A scenario whose checkpoint raises propagates
    (``ScenarioError``); a harness bug comes back as ``Outcome.HARNESS``."""
    today = date.today()
    slots = date_slots(today)
    sample = Sample(f"frontier-{scenario.name}", index, seed)
    saved = runtime.session_factory
    runtime.session_factory = sample.db
    error: BaseException | None = None
    verdict: Grade | None = None
    changes: dict[str, Any] | None = None
    try:
        if scenario.setup is not None:
            scenario.setup(sample)
        history = HISTORIES[scenario.history](today) if scenario.history else []
        thread = sample.converse(
            [(ask.format(**slots), _no_check) for ask in variant.asks], thread=history
        )
        episode = Episode(variant.name, thread, sample, slots)
        verdict = grade(
            episode, scenario.checkpoints, stopped=episode.stopped, silent=episode.silent
        )
        changes = episode.diff()
    except _RunDied:
        pass  # the sample says why; classify reads it
    except ScenarioError:
        raise
    except Exception as exc:
        error = exc
    finally:
        runtime.session_factory = saved
        sample.engine.dispose()
    outcome = classify(
        status_code=sample.status_code,
        stop_reason=sample.stop_reason,
        error=error,
        provider_failure=sample.provider_failure,
    )
    if outcome in (Outcome.PASS, Outcome.FAIL) and verdict is None:
        # The route failed a run (a tool raised) before the thread finished:
        # scored, the agent's doing, with nothing left to grade.
        verdict = missed_all(scenario.checkpoints)
    return Taken(outcome, sample, verdict, error, changes)


def measure(
    scenario: Scenario,
    *,
    runs: int,
    split: str,
    seed_base: int | None = None,
    infra_retries: int = INFRA_RETRIES,
    log: Callable[[str], None] = print,
) -> FrontierResult:
    """``runs`` samples of ``scenario``, cycling through ``split``'s wordings,
    each on a fresh DB. Never judges the count: the caller reports it.

    A provider death is re-taken (bounded by ``infra_retries``) and counted,
    never scored; a request the server rejected ends the scenario at once (it
    would be refused the same way every time). A harness bug raises. Sample
    *j* runs wording *j* with seed ``seed_base + j``, both keyed on the scored
    position, so a re-taken death draws the same wording and stream.
    """
    variants = scenario.variants(split)
    result = FrontierResult(
        scenario=scenario.name,
        tier=scenario.tier,
        split=split,
        checkpoints=tuple(c.name for c in scenario.checkpoints),
        fingerprint=scenario.fingerprint(split),
    )
    attempts = 0
    while result.runs < runs:
        variant = variants[result.runs % len(variants)]
        seed = sample_seed(seed_base, result.runs)
        attempts += 1
        taken = play(scenario, variant, attempts, seed)
        if taken.outcome is Outcome.HARNESS:
            assert taken.error is not None
            raise taken.error
        if taken.outcome in (Outcome.INFRA, Outcome.PROVIDER_REJECTED):
            result.infra += 1
            log(f"[frontier]   ⟳ infra ({result.infra}): {taken.sample.detail}")
            if taken.outcome is Outcome.PROVIDER_REJECTED or result.infra >= infra_retries:
                break
            continue
        assert taken.grade is not None
        claims = Tally()
        for step, claim in taken.sample.claims:
            where: dict[str, Any] = {"scenario": scenario.name, "variant": variant.name}
            if step:
                where["step"] = step
            claims.add([claim], **where)
        result.claims.merge(claims)
        result.add(
            taken.grade,
            {
                **taken.sample.record(taken.outcome, taken.error),
                "variant": variant.name,
                "changes": taken.changes,
            },
        )
        mark = "✓" if taken.grade.whole else "·"
        log(
            f"[frontier]   {mark} {scenario.name}[{variant.name}] {taken.grade.score:.2f} "
            f"mode={taken.grade.failure_mode} missed={taken.grade.missed}"
        )
    return result


def all_wordings(scenarios: Sequence[Scenario]) -> Iterator[tuple[Scenario, str, Variant]]:
    """Every (scenario, split, wording) in the corpus."""
    for scenario in scenarios:
        for split in ("dev", "heldout"):
            for variant in scenario.variants(split):
                yield scenario, split, variant
