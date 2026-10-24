"""The agent's state block (#412): the calendar, the snapshot, the text.

Pure date math and rendering are tested directly; the snapshot against a
seeded DB, where its counts must agree with the dashboard's (one roll-up, by
effective status); ``build`` must never fail, falling back to the calendar.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import state_block
from app.db.models import TaskWorkflowStatus
from app.services import dashboard
from app.services import projects as projects_service
from app.services import task_dependencies
from app.services import tasks as tasks_service
from app.services.dashboard import AgentSnapshot, RosterEntry
from app.tools import runtime

WEDNESDAY = date(2026, 10, 21)


# --- The calendar -------------------------------------------------------------


def test_a_midweek_calendar_names_every_day_with_its_weekday() -> None:
    assert state_block.calendar_lines(WEDNESDAY) == [
        "Today: Wednesday 2026-10-21",
        "Tomorrow: Thursday 2026-10-22",
        "Coming days: Friday 2026-10-23, Saturday 2026-10-24, Sunday 2026-10-25,"
        " Monday 2026-10-26, Tuesday 2026-10-27, Wednesday 2026-10-28",
        "Next Monday: Monday 2026-10-26",
        "End of this month: Saturday 2026-10-31",
    ]


@pytest.mark.parametrize("offset", range(7))
def test_every_weekday_has_one_date_strictly_after_today(offset: int) -> None:
    """Tomorrow and the coming days are the next seven (#484): on a Friday,
    next Friday is in the block, not only today."""
    today = WEDNESDAY + timedelta(days=offset)
    lines = state_block.calendar_lines(today)
    ahead = [today + timedelta(days=n) for n in range(1, 8)]
    named = lines[1].removeprefix("Tomorrow: ") + ", " + lines[2].removeprefix("Coming days: ")
    assert named == ", ".join(state_block.day(when) for when in ahead)


def test_a_friday_names_next_friday() -> None:
    assert state_block.calendar_lines(date(2026, 10, 23))[2].endswith("Friday 2026-10-30")


@pytest.mark.parametrize("today", [date(2026, 10, 24), date(2026, 10, 25)])
def test_next_monday_from_the_weekend_is_the_coming_one(today: date) -> None:
    assert "Next Monday: Monday 2026-10-26" in state_block.calendar_lines(today)


def test_next_monday_on_a_monday_is_a_week_on() -> None:
    assert "Next Monday: Monday 2026-10-26" in state_block.calendar_lines(date(2026, 10, 19))


def test_the_last_day_of_the_month_is_its_own_end_and_tomorrow_rolls_over() -> None:
    lines = state_block.calendar_lines(date(2026, 12, 31))
    assert "Tomorrow: Friday 2027-01-01" in lines
    assert "End of this month: Thursday 2026-12-31" in lines


def test_february_ends_on_the_leap_day() -> None:
    assert "End of this month: Tuesday 2028-02-29" in state_block.calendar_lines(
        date(2028, 2, 3)
    )


# --- The text -----------------------------------------------------------------


def _snapshot(count: int, name: str = "Garage") -> AgentSnapshot:
    roster = [
        RosterEntry(project_id=index + 1, name=f"{name} {index}", open_count=3, overdue_count=1)
        for index in range(count)
    ]
    return AgentSnapshot(roster=roster, overdue=2, due_today=1, blocked=4)


def test_render_lists_the_roster_and_the_counts_between_the_markers() -> None:
    text = state_block.render(WEDNESDAY, _snapshot(2))
    lines = text.splitlines()
    assert lines[0].startswith("[State at the start of this run")
    assert lines[-1] == "[End of state]"
    assert "- 1 · Garage 0 · 3 open · 1 overdue" in lines
    assert "- 2 · Garage 1 · 3 open · 1 overdue" in lines
    assert "Open tasks: 2 overdue, 1 due today, 4 blocked." in lines


def test_a_long_roster_is_capped_with_a_pointer_to_list_projects() -> None:
    text = state_block.render(WEDNESDAY, _snapshot(state_block.MAX_ROSTER + 3))
    rows = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(rows) == state_block.MAX_ROSTER + 1
    assert rows[-1] == "- …and 3 more (list_projects has them all)"


def test_a_long_name_is_clipped() -> None:
    text = state_block.render(WEDNESDAY, _snapshot(1, name="x" * 500))
    [row] = [line for line in text.splitlines() if line.startswith("- ")]
    assert "x" * state_block.MAX_NAME_CHARS not in row
    assert "x" * (state_block.MAX_NAME_CHARS - 1) + "…" in row


def test_no_projects_says_so() -> None:
    text = state_block.render(WEDNESDAY, AgentSnapshot([], 0, 0, 0))
    assert "Open projects: none yet." in text


def test_no_snapshot_leaves_the_calendar_alone() -> None:
    text = state_block.render(WEDNESDAY, None)
    assert "Today: Wednesday 2026-10-21" in text
    assert "projects" not in text.lower() and "Open tasks" not in text


# --- The snapshot -------------------------------------------------------------


def test_the_snapshot_counts_by_effective_status(db_session: Session) -> None:
    """Open, overdue, due today and blocked, by the same roll-up the dashboard
    uses: a checklist parent whose children are done is done, a done task is
    not overdue, a closed or trashed project is off the roster."""
    today = WEDNESDAY
    garage = projects_service.create_project(db_session, name="Garage")
    home = projects_service.create_project(db_session, name="Home")
    closed = projects_service.create_project(db_session, name="Old")
    trashed = projects_service.create_project(db_session, name="Gone")

    late = tasks_service.create_task(
        db_session, project_id=garage.id, title="Late", due_date=today - timedelta(days=2)
    )
    tasks_service.create_task(db_session, project_id=garage.id, title="Today", due_date=today)
    waiting = tasks_service.create_task(db_session, project_id=garage.id, title="Waits")
    task_dependencies.add_dependency(db_session, waiting.id, late.id)
    done_late = tasks_service.create_task(
        db_session, project_id=home.id, title="Done late", due_date=today - timedelta(days=5)
    )
    tasks_service.update_task(db_session, done_late, {"workflow_status": TaskWorkflowStatus.done})
    parent = tasks_service.create_task(
        db_session, project_id=home.id, title="Checklist", due_date=today - timedelta(days=1)
    )
    child = tasks_service.create_task(
        db_session, project_id=home.id, title="Step", parent_task_id=parent.id
    )
    tasks_service.update_task(db_session, child, {"workflow_status": TaskWorkflowStatus.done})
    tasks_service.create_task(db_session, project_id=closed.id, title="Closed lane")
    projects_service.close_project(db_session, closed)
    projects_service.soft_delete_project(db_session, trashed)
    db_session.commit()

    snapshot = dashboard.get_agent_snapshot(db_session, today)

    assert [(e.name, e.open_count, e.overdue_count) for e in snapshot.roster] == [
        ("Garage", 3, 1),
        ("Home", 0, 0),
    ]
    assert snapshot.roster[0].project_id == garage.id
    # The closed lane's task is still open, as the dashboard's total counts it.
    assert (snapshot.overdue, snapshot.due_today, snapshot.blocked) == (1, 1, 1)
    total, per_project = dashboard.get_overview(db_session)
    assert total == sum(e.open_count for e in snapshot.roster) + 1
    open_by_id = {counts.project.id: counts.open_count for counts in per_project}
    for entry in snapshot.roster:
        assert open_by_id[entry.project_id] == entry.open_count


def test_build_reads_through_the_tool_session_factory(
    db_session: Session, test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_service.create_project(db_session, name="Reading")
    db_session.commit()
    factory: sessionmaker[Session] = sessionmaker(bind=test_engine)
    monkeypatch.setattr(runtime, "session_factory", factory)

    text = state_block.build(WEDNESDAY)
    assert "· Reading · 0 open · 0 overdue" in text


def test_a_failing_snapshot_falls_back_to_the_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(db: Session, today: date) -> AgentSnapshot:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(dashboard, "get_agent_snapshot", broken)
    text = state_block.build(WEDNESDAY)
    assert text == state_block.render(WEDNESDAY, None)
