"""No-op mutations must not append activity events (issue #218).

``activity_events`` is append-only and is the app's audit trail, so every row in
it has to correspond to a state change that actually landed. A PATCH carrying no
fields (or only values the row already holds), and an idempotent
complete/reopen call on a task already in that state, change nothing — and must
therefore record nothing, on all three write boundaries: REST, the service layer,
and the agent tools.

The chosen semantics for a no-op PATCH is a **200 no-op**, not a 4xx: it matches
``projects.close_project``/``reopen_project`` (silently idempotent since they
were written) and the empty-body decision already pinned by
``test_strict_mutation_schemas.test_empty_patch_body_is_still_accepted``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActivityEvent, Task, TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.tools import registry, runtime


def _events(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(ActivityEvent)) or 0


def _actions(db: Session, task_id: int) -> list[str]:
    db.expire_all()
    return list(
        db.scalars(
            select(ActivityEvent.action)
            .where(ActivityEvent.entity_type == "task", ActivityEvent.entity_id == task_id)
            .order_by(ActivityEvent.id)
        ).all()
    )


def _tool_session(
    db_session: Session, test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the agent tools' per-call sessions at the test engine.

    The in-memory engine hands out one connection (StaticPool), so the test
    session's transaction is released first or the tool's session deadlocks.
    """
    db_session.close()
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)


# --- Service layer -----------------------------------------------------------


def test_empty_task_patch_records_nothing(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(db_session, project_id=project.id, title="Ship")
    db_session.commit()
    before = _events(db_session)

    tasks_service.update_task(db_session, task, {})
    db_session.commit()

    assert _events(db_session) == before


def test_control_only_task_patch_records_nothing(db_session: Session) -> None:
    """``{"edit_scope": "future"}`` is all control flag: zero edits reach the row."""
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(db_session, project_id=project.id, title="Ship")
    db_session.commit()
    before = _events(db_session)

    tasks_service.update_task(db_session, task, {"edit_scope": "future"})
    db_session.commit()

    assert _events(db_session) == before


def test_same_value_task_patch_records_nothing(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="Ship", description="why"
    )
    db_session.commit()
    before = _events(db_session)

    tasks_service.update_task(
        db_session, task, {"title": "Ship", "description": "why"}
    )
    db_session.commit()

    assert _events(db_session) == before


def test_mark_done_on_an_already_done_leaf_records_nothing(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="Already done",
        workflow_status=TaskWorkflowStatus.done,
    )
    db_session.commit()
    before = _events(db_session)

    tasks_service.mark_done(db_session, task)
    db_session.commit()

    assert _events(db_session) == before
    assert task.workflow_status == TaskWorkflowStatus.done


def test_reopen_on_an_already_open_leaf_records_nothing(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(db_session, project_id=project.id, title="Open")
    db_session.commit()
    before = _events(db_session)

    tasks_service.reopen_task(db_session, task)
    db_session.commit()

    assert _events(db_session) == before
    assert task.workflow_status == TaskWorkflowStatus.open


def test_empty_and_same_value_project_patches_record_nothing(
    db_session: Session,
) -> None:
    project = projects_service.create_project(
        db_session, name="Audit", description="the trail"
    )
    db_session.commit()
    before = _events(db_session)

    projects_service.update_project(db_session, project, {})
    projects_service.update_project(db_session, project, {"name": "Audit"})
    projects_service.update_project(
        db_session, project, {"name": "Audit", "description": "the trail"}
    )
    db_session.commit()

    assert _events(db_session) == before


# --- Real mutations are untouched --------------------------------------------


def test_a_real_edit_still_records_an_updated_event(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(db_session, project_id=project.id, title="Ship")
    db_session.commit()

    tasks_service.update_task(db_session, task, {"title": "Ship it"})
    projects_service.update_project(db_session, project, {"description": "now set"})
    db_session.commit()

    assert _actions(db_session, task.id) == ["created", "updated"]
    project_actions = db_session.scalars(
        select(ActivityEvent.action)
        .where(
            ActivityEvent.entity_type == "project",
            ActivityEvent.entity_id == project.id,
        )
        .order_by(ActivityEvent.id)
    ).all()
    assert list(project_actions) == ["created", "updated"]


def test_completing_a_recurring_task_still_rolls_the_series_forward(
    db_session: Session,
) -> None:
    """Recurrence reconciliation is untouched by the no-op guard."""
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="Water plants",
        due_date=date(2030, 1, 1),
    )
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "day", "every": 1}}
    )
    db_session.commit()

    tasks_service.mark_done(db_session, task)
    db_session.commit()

    assert "completed" in _actions(db_session, task.id)
    successors = db_session.scalars(
        select(Task).where(
            Task.recurrence_id == task.recurrence_id, Task.id != task.id
        )
    ).all()
    assert [t.due_date for t in successors] == [date(2030, 1, 2)]


def test_future_scope_still_patches_and_logs_occurrences_it_moves(
    db_session: Session,
) -> None:
    """A same-valued *head* still forwards: the later rows may be out of step."""
    project = projects_service.create_project(db_session, name="Audit")
    head = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="Water plants",
        due_date=date.today(),
    )
    tasks_service.update_task(
        db_session, head, {"repeat_interval": {"unit": "day", "every": 1}}
    )
    tasks_service.mark_done(db_session, head)
    db_session.commit()
    successor = db_session.scalars(
        select(Task).where(
            Task.recurrence_id == head.recurrence_id, Task.id != head.id
        )
    ).one()
    # The head already carries this title; the successor does not.
    tasks_service.update_task(
        db_session, successor, {"title": "Water all the plants"}
    )
    db_session.commit()

    later = tasks_service.update_task(
        db_session,
        successor,
        {"title": "Water all the plants", "edit_scope": "future"},
    )
    db_session.commit()

    assert later.title == "Water all the plants"
    # Nothing moved anywhere in the series, so nothing is recorded.
    assert _actions(db_session, successor.id) == ["created", "updated"]


def test_future_scope_logs_only_the_occurrences_that_change(
    db_session: Session,
) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    head = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="Water plants",
        due_date=date.today(),
    )
    tasks_service.update_task(
        db_session, head, {"repeat_interval": {"unit": "day", "every": 1}}
    )
    tasks_service.mark_done(db_session, head)
    db_session.commit()
    successor = db_session.scalars(
        select(Task).where(
            Task.recurrence_id == head.recurrence_id, Task.id != head.id
        )
    ).one()
    successor_id = successor.id
    before_head = _actions(db_session, head.id)

    tasks_service.update_task(
        db_session,
        successor,
        {"title": "Water all the plants", "edit_scope": "future"},
    )
    db_session.commit()

    # The successor really changed, so it is logged; the (earlier, already-done)
    # head is outside the forward window and gains nothing.
    assert _actions(db_session, successor_id)[-1] == "updated"
    assert _actions(db_session, head.id) == before_head


# --- REST boundary -----------------------------------------------------------


def test_rest_no_op_task_patches_are_200_and_record_nothing(
    client: TestClient, db_session: Session
) -> None:
    project_id = client.post("/api/projects", json={"name": "Audit"}).json()["id"]
    task = client.post(
        f"/api/projects/{project_id}/tasks", json={"title": "Ship"}
    ).json()
    before = _events(db_session)

    for payload in ({}, {"edit_scope": "future"}, {"title": "Ship"}):
        response = client.patch(f"/api/tasks/{task['id']}", json=payload)
        assert response.status_code == 200, payload
        assert response.json()["title"] == "Ship"

    assert _events(db_session) == before


def test_rest_no_op_project_patch_is_200_and_records_nothing(
    client: TestClient, db_session: Session
) -> None:
    project = client.post(
        "/api/projects", json={"name": "Audit", "description": "the trail"}
    ).json()
    before = _events(db_session)

    for payload in ({}, {"name": "Audit"}, {"description": "the trail"}):
        response = client.patch(f"/api/projects/{project['id']}", json=payload)
        assert response.status_code == 200, payload
        assert response.json()["name"] == "Audit"

    assert _events(db_session) == before


def test_rest_done_and_reopen_are_idempotent_and_record_one_event_each(
    client: TestClient, db_session: Session
) -> None:
    project_id = client.post("/api/projects", json={"name": "Audit"}).json()["id"]
    task_id = client.post(
        f"/api/projects/{project_id}/tasks", json={"title": "Ship"}
    ).json()["id"]

    # Reopening an already-open task is a no-op.
    assert client.post(f"/api/tasks/{task_id}/reopen").status_code == 200
    assert _actions(db_session, task_id) == ["created"]

    for _ in range(3):
        assert client.post(f"/api/tasks/{task_id}/done").status_code == 200
    assert _actions(db_session, task_id) == ["created", "completed"]

    for _ in range(3):
        assert client.post(f"/api/tasks/{task_id}/reopen").status_code == 200
    assert _actions(db_session, task_id) == ["created", "completed", "reopened"]


def test_rest_no_op_patch_does_not_bump_updated_at(
    client: TestClient, db_session: Session
) -> None:
    project_id = client.post("/api/projects", json={"name": "Audit"}).json()["id"]
    task = client.post(
        f"/api/projects/{project_id}/tasks", json={"title": "Ship"}
    ).json()

    patched = client.patch(f"/api/tasks/{task['id']}", json={})

    assert patched.status_code == 200
    assert patched.json()["updated_at"] == task["updated_at"]


# --- Agent tools -------------------------------------------------------------


def test_agent_tool_no_op_task_update_records_nothing(
    db_session: Session, test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="Ship",
        workflow_status=TaskWorkflowStatus.done,
    )
    task_id = task.id
    db_session.commit()
    before = _events(db_session)
    _tool_session(db_session, test_engine, monkeypatch)

    registry.call_tool("update_task", {"task_id": task_id, "changes": {}}, actor="agent")
    registry.call_tool(
        "update_task",
        {"task_id": task_id, "changes": {"title": "Ship", "edit_scope": "future"}},
        actor="agent",
    )
    registry.call_tool("complete_task", {"task_id": task_id}, actor="agent")

    assert _events(db_session) == before


def test_agent_tool_no_op_project_update_records_nothing(
    db_session: Session, test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    project_id = project.id
    db_session.commit()
    before = _events(db_session)
    _tool_session(db_session, test_engine, monkeypatch)

    registry.call_tool(
        "update_project", {"project_id": project_id, "changes": {}}, actor="agent"
    )
    registry.call_tool(
        "update_project",
        {"project_id": project_id, "changes": {"name": "Audit"}},
        actor="agent",
    )

    assert _events(db_session) == before


def test_agent_tool_reopen_on_an_open_task_records_nothing(
    db_session: Session, test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(db_session, project_id=project.id, title="Ship")
    task_id = task.id
    db_session.commit()
    before = _events(db_session)
    _tool_session(db_session, test_engine, monkeypatch)

    registry.call_tool("reopen_task", {"task_id": task_id}, actor="agent")

    assert _events(db_session) == before


def test_agent_tool_real_update_still_records_an_event(
    db_session: Session, test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = projects_service.create_project(db_session, name="Audit")
    task = tasks_service.create_task(db_session, project_id=project.id, title="Ship")
    task_id = task.id
    db_session.commit()
    _tool_session(db_session, test_engine, monkeypatch)

    registry.call_tool(
        "update_task",
        {"task_id": task_id, "changes": {"due_date": str(date.today() + timedelta(1))}},
        actor="agent",
    )

    assert _actions(db_session, task_id) == ["created", "updated"]
