"""Quoted outside titles in agent-written task titles (#522, #520).

Conductor fills ``{s1}`` with another app's title in curly quotes ("add a task
to sort “Honey” this weekend"). The agent keeps the quotes around that title
but every wrapping quote becomes a straight double quote, in code, where the
agent's tools write a task title; apostrophes inside words stay. The UI's own
writes keep what the user typed.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.services import tasks as tasks_service
from app.tools import registry, runtime
from app.tools.titles import straight_quotes


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("Sort “Honey” this weekend", 'Sort "Honey" this weekend'),
        ("Sort ‘Honey’ this weekend", 'Sort "Honey" this weekend'),
        ("Sort 'Honey' this weekend", 'Sort "Honey" this weekend'),
        ('Sort "Honey" this weekend', 'Sort "Honey" this weekend'),
        ("Sort the album 'Honey'", 'Sort the album "Honey"'),
        ("„Honey‟ and «Swim»", '"Honey" and «Swim»'),
        ("Sort 'Honey' and 'Swim'", 'Sort "Honey" and "Swim"'),
        ("Sort 'Caribou's Honey'", "Sort \"Caribou's Honey\""),
        # Apostrophes inside or at the edge of a word are never quotes.
        ("Don't forget Caribou's album", "Don't forget Caribou's album"),
        ("Don’t forget Caribou’s album", "Don’t forget Caribou’s album"),
        ("Tidy the kids' toys", "Tidy the kids' toys"),
        ("Play '90s hits", "Play '90s hits"),
        ("Rock 'n' roll night", "Rock 'n' roll night"),
        ("Water the plants", "Water the plants"),
    ],
)
def test_straight_quotes(given: str, stored: str) -> None:
    assert straight_quotes(given) == stored


def test_the_agent_stores_an_outside_title_in_straight_double_quotes(
    tool_db: sessionmaker[Session],
) -> None:
    created = registry.call_tool(
        "create_task", {"title": "Sort “Honey” this weekend"}, actor="agent:conductor"
    )
    assert created.title == 'Sort "Honey" this weekend'

    renamed = registry.call_tool(
        "update_task",
        {"task_id": created.id, "title": "Sort ‘Swim’ next"},
        actor="agent:conductor",
    )
    assert renamed.title == 'Sort "Swim" next'
    with tool_db() as db:
        task = tasks_service.get_task(db, created.id)
        assert task is not None and task.title == 'Sort "Swim" next'


def test_the_ui_keeps_the_title_as_typed(client: TestClient) -> None:
    response = client.post("/api/tasks", json={"title": "Read “Dune”"})
    assert response.status_code == 201, response.text
    assert response.json()["title"] == "Read “Dune”"
