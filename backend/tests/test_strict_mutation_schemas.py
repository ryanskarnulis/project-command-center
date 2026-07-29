"""Unknown fields are rejected at every mutation boundary (#164).

Before this, the mutation models kept Pydantic's default ``extra="ignore"``: a
typo (``prioirty``) validated cleanly, the create route applied defaults, and a
PATCH whose only key was misspelled reduced to an empty field map that
``update_task`` still flushed and logged an ``updated`` activity event for. The
audit trail claimed a write that never happened.

These tests pin the three halves of the fix: REST returns 422 and writes
nothing, the agent tool surface rejects the same typo nested inside its
argument models, and the omit-vs-null semantics of a *valid* partial PATCH are
untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActivityEvent, Project, Task
from app.mcp.server import mcp
from app.schemas.projects import ProjectCreate, ProjectUpdate
from app.schemas.task_dependencies import TaskDependencyCreate
from app.schemas.tasks import TaskCreate, TaskUpdate


def _counts(db: Session) -> tuple[int, int, int]:
    return (
        db.execute(select(func.count()).select_from(Task)).scalar_one(),
        db.execute(select(func.count()).select_from(Project)).scalar_one(),
        db.execute(select(func.count()).select_from(ActivityEvent)).scalar_one(),
    )


# --- Model level -------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "payload", "unknown"),
    [
        (TaskCreate, {"title": "Ship", "prioirty": "urgent"}, "prioirty"),
        (TaskUpdate, {"workflow_stats": "done"}, "workflow_stats"),
        (ProjectCreate, {"name": "Roadmap", "descripton": "lost"}, "descripton"),
        (ProjectUpdate, {"nmae": "Roadmap"}, "nmae"),
        (TaskDependencyCreate, {"depends_on_task_id": 1, "kind": "blocks"}, "kind"),
    ],
)
def test_mutation_models_reject_unknown_fields(
    model: type[TaskCreate | TaskUpdate | ProjectCreate | ProjectUpdate | TaskDependencyCreate],
    payload: dict[str, object],
    unknown: str,
) -> None:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    errors = exc.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)
    assert any(unknown in e["loc"] for e in errors)


def test_nested_repeat_interval_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TaskUpdate.model_validate(
            {"repeat_interval": {"unit": "week", "every": 2, "untl": "2030-01-01"}}
        )


# --- REST boundary -----------------------------------------------------------


def test_create_task_rejects_unknown_field_without_writing(
    client: TestClient, db_session: Session
) -> None:
    before = _counts(db_session)

    response = client.post("/api/tasks", json={"title": "Ship", "prioirty": "urgent"})

    assert response.status_code == 422
    body = response.json()
    assert any("prioirty" in str(d["loc"]) for d in body["detail"])
    assert _counts(db_session) == before


def test_patch_task_rejects_unknown_field_and_logs_no_event(
    client: TestClient, db_session: Session
) -> None:
    """The headline regression: a misspelled PATCH key must not log an update."""
    created = client.post("/api/tasks", json={"title": "Ship"})
    assert created.status_code == 201
    task_id = created.json()["id"]
    before = _counts(db_session)

    response = client.patch(f"/api/tasks/{task_id}", json={"workflow_stats": "done"})

    assert response.status_code == 422
    assert _counts(db_session) == before
    task = db_session.get(Task, task_id)
    assert task is not None
    assert task.workflow_status.value == "open"


def test_create_project_rejects_unknown_field_without_writing(
    client: TestClient, db_session: Session
) -> None:
    before = _counts(db_session)

    response = client.post(
        "/api/projects", json={"name": "Roadmap", "descripton": "lost"}
    )

    assert response.status_code == 422
    assert _counts(db_session) == before


def test_patch_project_rejects_unknown_field_and_logs_no_event(
    client: TestClient, db_session: Session
) -> None:
    created = client.post("/api/projects", json={"name": "Roadmap"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    before = _counts(db_session)

    response = client.patch(f"/api/projects/{project_id}", json={"nmae": "Renamed"})

    assert response.status_code == 422
    assert _counts(db_session) == before
    project = db_session.get(Project, project_id)
    assert project is not None
    assert project.name == "Roadmap"


def test_add_dependency_rejects_unknown_field_without_writing(
    client: TestClient, db_session: Session
) -> None:
    first = client.post("/api/tasks", json={"title": "First"}).json()
    second = client.post("/api/tasks", json={"title": "Second"}).json()
    before = _counts(db_session)

    response = client.post(
        f"/api/tasks/{second['id']}/dependencies",
        json={"depends_on_task_id": first["id"], "kind": "blocks"},
    )

    assert response.status_code == 422
    assert _counts(db_session) == before


# --- Existing semantics are unchanged ----------------------------------------


def test_valid_partial_patch_still_applies_only_sent_fields(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/tasks", json={"title": "Ship", "priority": "high", "description": "why"}
    ).json()

    response = client.patch(f"/api/tasks/{created['id']}", json={"title": "Shipped"})

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Shipped"
    # Omitted fields untouched.
    assert body["priority"] == "high"
    assert body["description"] == "why"


def test_omit_versus_explicit_null_still_differ(client: TestClient) -> None:
    created = client.post(
        "/api/tasks", json={"title": "Ship", "description": "why", "due_date": "2030-01-01"}
    ).json()
    task_id = created["id"]

    # Omitted: left alone.
    omitted = client.patch(f"/api/tasks/{task_id}", json={"title": "Ship it"})
    assert omitted.status_code == 200
    assert omitted.json()["description"] == "why"
    assert omitted.json()["due_date"] == "2030-01-01"

    # Explicit null on a nullable column: cleared.
    cleared = client.patch(f"/api/tasks/{task_id}", json={"description": None})
    assert cleared.status_code == 200
    assert cleared.json()["description"] is None
    assert cleared.json()["due_date"] == "2030-01-01"

    # Explicit null on a NOT-NULL column: still a 422, not a 500.
    rejected = client.patch(f"/api/tasks/{task_id}", json={"title": None})
    assert rejected.status_code == 422


def test_empty_patch_body_is_still_accepted(
    client: TestClient, db_session: Session
) -> None:
    """``{}`` is a well-formed no-op request and stays a 200 (see PR notes).

    A no-op stays a no-op all the way down: issue #218 made the *service* stop
    logging for it too, so the accepted-but-empty PATCH writes no activity row
    either. ``tests/test_no_op_activity.py`` covers that rule across all three
    boundaries; this asserts the 200 half of it does not regress here.
    """
    created = client.post("/api/tasks", json={"title": "Ship"}).json()
    before = _counts(db_session)

    response = client.patch(f"/api/tasks/{created['id']}", json={})

    assert response.status_code == 200
    assert response.json()["title"] == "Ship"
    db_session.expire_all()
    assert _counts(db_session) == before


# --- Agent tool boundary -----------------------------------------------------


@asynccontextmanager
async def _mcp_client() -> AsyncIterator[ClientSession]:
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        yield client


@pytest.fixture
def _mcp_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> sessionmaker[Session]:
    from app.tools import runtime

    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    return factory


async def test_agent_tool_rejects_unknown_nested_mutation_field(
    _mcp_db: sessionmaker[Session],
) -> None:
    """``create_task``/``update_task`` take ``TaskCreate``/``TaskUpdate`` nested in
    their argument models, so the typo is caught as a schema error before any
    session is opened."""
    async with _mcp_client() as client:
        created = await client.call_tool(
            "create_task", {"data": {"title": "Ship", "prioirty": "urgent"}}
        )
        assert created.isError
        assert created.content[0].type == "text"
        assert "prioirty" in created.content[0].text

        updated = await client.call_tool(
            "update_task", {"task_id": 1, "changes": {"workflow_stats": "done"}}
        )
        # Must fail on the *schema*, not on "task 1 not found" — otherwise this
        # would still pass with extra="ignore".
        assert updated.isError
        assert updated.content[0].type == "text"
        assert "workflow_stats" in updated.content[0].text

    with _mcp_db() as db:
        # Nothing was written by either rejected call.
        assert db.execute(select(func.count()).select_from(Task)).scalar_one() == 0
        assert (
            db.execute(select(func.count()).select_from(ActivityEvent)).scalar_one() == 0
        )


def test_agent_tool_registry_call_raises_validation_error(
    _mcp_db: sessionmaker[Session],
) -> None:
    """The in-app loop's dispatch path raises the same schema error it feeds back."""
    from app.tools import registry

    with pytest.raises(ValidationError):
        registry.call_tool(
            "create_project",
            {"data": {"name": "Roadmap", "descripton": "lost"}},
            actor="agent:test",
        )
