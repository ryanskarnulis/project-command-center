"""The trimmed view the loop's model reads back from a tool call (#414).

The tools answer with the REST read models (what MCP and the persisted run
record keep); ``app/tools/views.py`` projects them for the local model: a task
row names its project, carries no timestamps or nulls, and shows a flag only
when it is true; a task list says how many match and whether there is more.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai.loop import AgentLoop
from app.schemas.tasks import TaskPage
from app.services import dashboard as dashboard_service
from app.tools import registry, runtime, views
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


def _call(name: str, arguments: dict[str, Any]) -> Any:
    return registry.call_tool(name, arguments, actor="agent:test")


def _view(name: str, arguments: dict[str, Any]) -> Any:
    return views.agent_view(_call(name, arguments))


def test_a_task_row_names_its_project_and_drops_nulls_and_timestamps(
    tool_db: sessionmaker[Session],
) -> None:
    project = _call("create_project", {"name": "Garage"})
    _call("create_task", {"title": "Sweep", "project_id": project.id})

    page = _view("list_tasks", {})

    assert page == {
        "tasks": [
            {
                "id": 1,
                "title": "Sweep",
                "project": "Garage",
                "status": "open",
                "priority": "medium",
            }
        ],
        "total": 1,
        "has_more": False,
    }


def test_flags_appear_only_when_true(tool_db: sessionmaker[Session]) -> None:
    project = _call("create_project", {"name": "Garage"})
    parent = _call("create_task", {"title": "Clean out", "project_id": project.id})
    child = _call("create_task", {"title": "Sweep", "parent_task_id": parent.id})
    blocker = _call(
        "create_task",
        {"title": "Buy broom", "project_id": project.id, "due_date": "2026-11-02"},
    )
    _call("add_dependency", {"task_id": child.id, "depends_on_task_id": blocker.id})

    rows = {row["title"]: row for row in _view("list_tasks", {})["tasks"]}

    assert rows["Clean out"].get("subtasks") is True
    assert rows["Sweep"]["parent"] == parent.id
    assert rows["Sweep"]["blocked"] is True
    assert rows["Buy broom"]["blocking"] is True
    assert rows["Buy broom"]["due"] == "2026-11-02"
    assert "blocked" not in rows["Buy broom"]
    assert "due" not in rows["Clean out"]


def test_a_write_answers_with_the_fields_it_can_change(
    tool_db: sessionmaker[Session],
) -> None:
    _call("create_project", {"name": "Garage"})
    task = _call("create_task", {"title": "Sweep", "project_id": 1})

    updated = _view(
        "update_task",
        {
            "task_id": task.id,
            "estimated_minutes": 30,
            "description": "Back corner too",
            "repeat_interval": {"unit": "week", "every": 1},
            "due_date": "2026-11-02",
        },
    )

    assert updated["estimated_minutes"] == 30
    assert updated["description"] == "Back corner too"
    assert updated["repeat"] == {"unit": "week", "every": 1}
    assert updated["project"] == "Garage"
    assert "created_at" not in updated and "recurring" not in updated


def test_a_trashed_task_still_names_its_trashed_project(
    tool_db: sessionmaker[Session],
) -> None:
    project = _call("create_project", {"name": "Shed"})
    task = _call("create_task", {"title": "Paint", "project_id": project.id})
    _call("trash_task", {"task_id": task.id})
    _call("trash_project", {"project_id": project.id})

    trash = _view("list_trash", {})

    assert trash["tasks"][0]["project"] == "Shed"
    assert trash["projects"] == [{"id": project.id, "name": "Shed", "tasks_trashed_with_it": 0}]


def test_projects_carry_their_open_task_count(
    tool_db: sessionmaker[Session],
) -> None:
    project = _call("create_project", {"name": "Garage"})
    _call("create_task", {"title": "Sweep", "project_id": project.id})
    done = _call("create_task", {"title": "Mop", "project_id": project.id})
    _call("complete_task", {"task_id": done.id})

    listed = _view("list_projects", {})

    with tool_db() as db:
        _, lanes = dashboard_service.get_overview(db)
    expected = {lane.project.id: lane.open_count for lane in lanes}
    assert {p["id"]: p["open_tasks"] for p in listed} == expected
    assert next(p for p in listed if p["id"] == project.id) == {
        "id": project.id,
        "name": "Garage",
        "open_tasks": 1,
    }


@pytest.mark.parametrize("arguments", [{}, {"exclude_done": True}])
def test_a_page_counts_every_match_and_says_whether_there_is_more(
    tool_db: sessionmaker[Session], arguments: dict[str, Any]
) -> None:
    _call("create_project", {"name": "Garage"})
    for n in range(5):
        _call("create_task", {"title": f"Task {n}", "project_id": 1})

    first = _call("list_tasks", {**arguments, "limit": 2})
    last = _call("list_tasks", {**arguments, "limit": 2, "offset": 4})

    assert isinstance(first, TaskPage)
    assert [t.title for t in first.items] == ["Task 0", "Task 1"]
    assert (first.total, first.has_more) == (5, True)
    assert [t.title for t in last.items] == ["Task 4"]
    assert (last.total, last.has_more) == (5, False)


def test_results_without_a_view_pass_through(tool_db: sessionmaker[Session]) -> None:
    _call("create_project", {"name": "Garage"})
    task = _call("create_task", {"title": "Sweep", "project_id": 1})

    message = _call("trash_task", {"task_id": task.id})

    assert views.agent_view(message) is message


def test_the_model_reads_the_view_and_the_record_keeps_the_read_model(
    tool_db: sessionmaker[Session],
) -> None:
    provider = ScriptedProvider(
        [
            _calls(("create_project", {"name": "Garage"})),
            _calls(("create_task", {"title": "Sweep", "project_id": 1})),
            _text("Done."),
        ]
    )

    result = AgentLoop(provider).run("Add a Sweep task to a new Garage project.")

    seen = json.loads(provider.requests[2]["messages"][-1]["content"])
    assert seen == {
        "id": 1,
        "title": "Sweep",
        "project": "Garage",
        "status": "open",
        "priority": "medium",
    }
    kept = json.loads(result.tool_calls[1].result or "")
    assert kept["project_id"] == 1 and "created_at" in kept
