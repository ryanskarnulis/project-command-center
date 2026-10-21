"""The payload tools take top-level arguments (#411).

``create_task``/``update_task``/``create_project``/``update_project`` advertise
their payload's fields at the top level, stay strict, keep omit-versus-null, and
forgive the shapes the model was seen to send: the ``data``/``changes`` wrapper
(at any depth), the other item's word for the name, and ``parent_id``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.mcp.server import mcp
from app.tools import registry, runtime


@pytest.fixture
def _tool_db(test_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    return factory


def _call(name: str, arguments: dict[str, Any]) -> Any:
    return registry.call_tool(name, arguments, actor="agent:test")


def test_payload_fields_are_top_level_arguments() -> None:
    params = registry.get_tool("update_task").parameters
    assert list(params["properties"])[:2] == ["task_id", "title"]
    assert "changes" not in params["properties"]
    assert "repeat_interval" in params["properties"]
    assert params["required"] == ["task_id"]
    assert params["additionalProperties"] is False
    assert "data" not in registry.get_tool("create_task").parameters["properties"]
    assert registry.get_tool("create_project").parameters["required"] == ["name"]


def test_the_schema_names_only_the_canonical_keys() -> None:
    assert "name" not in registry.get_tool("create_task").parameters["properties"]
    assert "parent_id" not in registry.get_tool("update_task").parameters["properties"]
    assert "title" not in registry.get_tool("update_project").parameters["properties"]


@pytest.mark.usefixtures("_tool_db")
def test_flat_create_and_update_round_trip() -> None:
    project = _call("create_project", {"name": "Garage"})
    task = _call("create_task", {"title": "Sweep", "project_id": project.id, "priority": "high"})
    assert (task.title, task.project_id, task.priority) == ("Sweep", project.id, "high")

    updated = _call("update_task", {"task_id": task.id, "due_date": "2026-11-02"})
    assert str(updated.due_date) == "2026-11-02"
    assert updated.priority == "high"  # omitted, untouched

    renamed = _call("update_project", {"project_id": project.id, "description": "Tools"})
    assert (renamed.name, renamed.description) == ("Garage", "Tools")


@pytest.mark.usefixtures("_tool_db")
def test_omitted_leaves_a_field_and_explicit_null_clears_it() -> None:
    task = _call("create_task", {"title": "Water plants", "due_date": "2026-11-02"})
    _call("update_task", {"task_id": task.id, "repeat_interval": {"unit": "week"}})

    kept = _call("update_task", {"task_id": task.id, "title": "Water the plants"})
    assert str(kept.due_date) == "2026-11-02"
    assert kept.repeat_interval is not None

    stopped = _call("update_task", {"task_id": task.id, "repeat_interval": None})
    assert stopped.repeat_interval is None
    cleared = _call("update_task", {"task_id": task.id, "due_date": None})
    assert cleared.due_date is None


@pytest.mark.usefixtures("_tool_db")
@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("create_task", {"title": "Ship", "prioirty": "urgent"}),
        ("update_task", {"task_id": 1, "workflow_stats": "done"}),
        ("update_task", {"task_id": 1, "title": None}),
        ("update_task", {"task_id": 1, "repeat_interval": {"unit": "week", "days": [0]}}),
        ("create_project", {"name": "Roadmap", "sort_order": 1}),
        ("create_task", {"data": {"title": "Ship"}, "priority": "high"}),
    ],
)
def test_still_strict(name: str, arguments: dict[str, Any]) -> None:
    _call("create_task", {"title": "Existing"})
    with pytest.raises(ValidationError):
        _call(name, arguments)


@pytest.mark.usefixtures("_tool_db")
def test_a_wrapper_is_lifted_at_any_depth() -> None:
    once = _call("create_task", {"data": {"title": "Wrapped once"}})
    twice = _call("create_task", {"data": {"data": {"title": "Wrapped twice"}}})
    assert (once.title, twice.title) == ("Wrapped once", "Wrapped twice")

    updated = _call("update_task", {"task_id": once.id, "changes": {"priority": "urgent"}})
    assert updated.priority == "urgent"
    project = _call("create_project", {"data": {"name": "Wrapped"}})
    renamed = _call("update_project", {"project_id": project.id, "changes": {"name": "Unwrapped"}})
    assert renamed.name == "Unwrapped"


@pytest.mark.usefixtures("_tool_db")
def test_the_other_items_word_for_the_name_is_accepted() -> None:
    parent = _call("create_task", {"name": "Named, not titled"})
    assert parent.title == "Named, not titled"
    child = _call("create_task", {"title": "Child", "parent_id": parent.id})
    assert child.parent_task_id == parent.id

    project = _call("create_project", {"title": "Titled, not named"})
    assert project.name == "Titled, not named"
    renamed = _call("update_project", {"project_id": project.id, "title": "Renamed"})
    assert renamed.name == "Renamed"


@pytest.mark.usefixtures("_tool_db")
def test_when_both_words_are_sent_the_canonical_one_wins() -> None:
    task = _call("create_task", {"title": "Title wins", "name": "Name loses"})
    assert task.title == "Title wins"


@pytest.mark.usefixtures("_tool_db")
def test_a_cadence_without_every_repeats_every_one() -> None:
    task = _call("create_task", {"title": "Weekly review", "due_date": "2026-11-02"})
    weekly = _call("update_task", {"task_id": task.id, "repeat_interval": {"unit": "week"}})
    assert weekly.repeat_interval is not None
    assert weekly.repeat_interval.model_dump() == {"unit": "week", "every": 1}

    standup = _call(
        "add_focus_commitment",
        {
            "title": "Standup",
            "start_time": "09:30",
            "duration_minutes": 15,
            "date": "2026-11-02",
            "repeat_interval": {"unit": "week", "days": [0, 1, 2, 3, 4]},
        },
    )
    assert standup.repeat_interval is not None
    assert standup.repeat_interval.model_dump() == {
        "unit": "week",
        "every": 1,
        "days": [0, 1, 2, 3, 4],
    }


def test_rest_still_requires_every(client: TestClient) -> None:
    task = client.post("/api/tasks", json={"title": "Weekly", "due_date": "2026-11-02"}).json()
    response = client.patch(f"/api/tasks/{task['id']}", json={"repeat_interval": {"unit": "week"}})
    assert response.status_code == 422


@asynccontextmanager
async def _mcp_client() -> AsyncIterator[ClientSession]:
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        yield client


async def test_mcp_advertises_and_accepts_the_flat_shape(
    _tool_db: sessionmaker[Session],
) -> None:
    async with _mcp_client() as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert listed["update_task"].inputSchema == registry.get_tool("update_task").parameters

        created = await client.call_tool("create_task", {"name": "Via MCP"})
        assert not created.isError, created.content
        assert created.structuredContent is not None
        assert created.structuredContent["title"] == "Via MCP"

        refused = await client.call_tool("create_task", {"title": "Ship", "prioirty": "urgent"})
        assert refused.isError
        assert refused.content[0].type == "text"
        assert "prioirty" in refused.content[0].text
