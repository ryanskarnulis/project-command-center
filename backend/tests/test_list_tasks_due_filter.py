"""``list_tasks`` filters by due date (#466).

The model's first call for "move everything due today" is
``list_tasks(due_date=<today>)``; before this it was refused as an unknown
argument and the run paid a correction round trip.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

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


def _titles(arguments: dict[str, Any]) -> list[str]:
    return [task.title for task in _call("list_tasks", arguments)]


@pytest.fixture
def _dated(_tool_db: sessionmaker[Session]) -> None:
    project = _call("create_project", {"name": "Garage"})
    for title, due in (
        ("Monday", "2026-10-19"),
        ("Today", "2026-10-21"),
        ("Today too", "2026-10-21"),
        ("Friday", "2026-10-23"),
        ("Someday", None),
    ):
        _call("create_task", {"title": title, "project_id": project.id, "due_date": due})
    done = _call("create_task", {"title": "Today, done", "project_id": project.id})
    _call("update_task", {"task_id": done.id, "due_date": "2026-10-21"})
    _call("complete_task", {"task_id": done.id})


@pytest.mark.usefixtures("_dated")
def test_due_date_matches_that_day() -> None:
    assert _titles({"due_date": "2026-10-21", "exclude_done": True}) == ["Today", "Today too"]
    assert _titles({"due_date": "2026-10-21"}) == ["Today", "Today too", "Today, done"]


@pytest.mark.usefixtures("_dated")
def test_due_before_and_after_are_exclusive() -> None:
    assert _titles({"due_before": "2026-10-21"}) == ["Monday"]
    assert _titles({"due_after": "2026-10-21"}) == ["Friday"]
    assert _titles({"due_after": "2026-10-19", "due_before": "2026-10-23", "exclude_done": True}) == [
        "Today",
        "Today too",
    ]


@pytest.mark.usefixtures("_dated")
def test_due_filter_pages_the_filtered_set() -> None:
    args = {"due_date": "2026-10-21", "exclude_done": True, "limit": 1}
    assert _titles(args) == ["Today"]
    assert _titles({**args, "offset": 1}) == ["Today too"]


@pytest.mark.usefixtures("_tool_db")
def test_a_bad_due_date_is_refused() -> None:
    with pytest.raises(ValidationError, match="due_date"):
        _call("list_tasks", {"due_date": "tomorrow"})
