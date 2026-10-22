"""Tool refusals that say how to recover (#413).

The registry's refusals carry ``retry`` and the way forward; the loop feeds
every failure back as one JSON shape; MCP clients read the same refusal as
text with a prose hint.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai.loop import AgentLoop
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service
from app.tools import registry, runtime
from app.tools.registry import ToolRefusal
from tests.scripted_provider import ScriptedProvider
from tests.scripted_provider import text_turn as _text
from tests.scripted_provider import tool_calls_turn as _calls


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


def _refuse(name: str, arguments: dict[str, Any]) -> ToolRefusal:
    with pytest.raises(ToolRefusal) as excinfo:
        registry.call_tool(name, arguments, actor="agent:mcp")
    return excinfo.value


def test_complete_parent_names_its_open_subtasks(tool_db: sessionmaker[Session]) -> None:
    with tool_db() as db:
        parent = tasks_service.create_task(db, project_id=None, title="Clean out the garage")
        sweep, sort, haul = (
            tasks_service.create_task(db, project_id=None, title=t, parent_task_id=parent.id)
            for t in ("Sweep", "Sort", "Haul")
        )
        tasks_service.mark_done(db, sweep)
        db.commit()
        ids = parent.id, sort.id, haul.id

    refusal = _refuse("complete_task", {"task_id": ids[0]})

    assert refusal.retry == "never"
    assert [ref["id"] for ref in refusal.details["open_subtasks"]] == list(ids[1:])
    assert "complete_task" in refusal.details["next"]
    # MCP reads the text: the reason, then the way forward.
    assert str(refusal).startswith(refusal.error)
    assert f'#{ids[1]} "Sort"' in str(refusal)


def test_blocked_task_names_its_blockers(tool_db: sessionmaker[Session]) -> None:
    with tool_db() as db:
        report = tasks_service.create_task(db, project_id=None, title="Write report")
        pull = tasks_service.create_task(db, project_id=None, title="Pull data")
        done = tasks_service.create_task(db, project_id=None, title="Book room")
        deps_service.add_dependency(db, report.id, pull.id)
        tasks_service.mark_done(db, done)
        deps_service.add_dependency(db, report.id, done.id)
        db.commit()
        report_id, pull_id = report.id, pull.id

    refusal = _refuse("complete_task", {"task_id": report_id})

    assert refusal.retry == "never"
    assert [ref["id"] for ref in refusal.details["blockers"]] == [pull_id]
    assert "remove_dependency" in refusal.details["next"]


def test_due_date_clash_points_at_the_occurrence(tool_db: sessionmaker[Session]) -> None:
    with tool_db() as db:
        project = projects_service.create_project(db, name="Recurring")
        first = tasks_service.create_task(
            db, project_id=project.id, title="water plants", due_date=date(2026, 7, 1)
        )
        tasks_service.update_task(db, first, {"repeat_interval": {"unit": "week", "every": 1}})
        tasks_service.mark_done(db, first)
        db.commit()
        first_id = first.id
        second = max(
            tasks_service.list_tasks(db, project.id), key=lambda t: t.due_date or date.min
        )
        second_id = second.id

    refusal = _refuse("update_task", {"task_id": second_id, "due_date": "2026-07-01"})

    assert refusal.retry == "different_args"
    assert refusal.details["conflicting_occurrence"]["id"] == first_id


def test_not_found_says_where_to_look(tool_db: sessionmaker[Session]) -> None:
    refusal = _refuse("get_task", {"task_id": 999})
    assert refusal.retry == "different_args"
    assert "search" in refusal.details["next"]
    assert _refuse("restore_task", {"task_id": 999}).details["next"].startswith(
        "See list_trash"
    )


def test_every_failure_reaches_the_model_in_one_shape(
    tool_db: sessionmaker[Session],
) -> None:
    provider = ScriptedProvider(
        [
            _calls(
                ("get_task", {"task_id": 999}),
                ("create_task", {"title": "   "}),
                ("no_such_tool", {}),
            ),
            _text("Done."),
        ]
    )
    result = AgentLoop(provider).run("Look around.")

    answers = [
        json.loads(m["content"]) for m in provider.requests[1]["messages"] if m["role"] == "tool"
    ]
    assert [(a["ok"], a["retry"]) for a in answers] == [
        (False, "different_args"),
        (False, "different_args"),
        (False, "never"),
    ]
    assert answers[0]["next"].startswith("Find the task's id")
    # The record keeps the bare reason; the run record reads it unchanged.
    assert result.tool_calls[0].error == "Task 999 not found"
    assert result.tool_calls[1].error is not None
    assert result.tool_calls[1].error.startswith("Invalid arguments: ")
