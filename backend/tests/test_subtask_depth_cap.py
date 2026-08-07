"""Subtask nesting is bounded at ``MAX_SUBTASK_DEPTH`` (#252).

Every subtree traversal in the service layer is recursive — roll-up resolution,
the soft-delete cascade, the recurrence clone/reschedule walks, the trash
depth-first walks — so an unbounded tree eventually blows the Python stack. The
walks don't all fail at the same depth either: creates used to commit chains that
DELETE could no longer process, leaving a subtree permanently stuck in the
database.

The fix rejects the *edge* at create and reparent time with a 422, so any state
the API can commit is shallow enough for every walk. These tests pin the
boundary (at the cap works, one past it is a 422 — for creates and for moves that
push a subtree's deepest leaf past the cap) and confirm a chain grown to exactly
the cap survives the whole lifecycle: list, read, delete, restore, purge.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.services.tasks import MAX_SUBTASK_DEPTH


def _project_id(client: TestClient) -> int:
    created = client.post("/api/projects", json={"name": "Nesting"})
    assert created.status_code == 201
    return int(created.json()["id"])


def _create(client: TestClient, project_id: int, title: str, parent: int | None) -> int:
    """Create a task through the API; asserts a 201 and returns the new id."""
    payload: dict[str, object] = {"project_id": project_id, "title": title}
    if parent is not None:
        payload["parent_task_id"] = parent
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _chain(client: TestClient, project_id: int, levels: int) -> list[int]:
    """Ids of a parent->child chain ``levels`` deep, root first."""
    ids: list[int] = []
    parent: int | None = None
    for level in range(1, levels + 1):
        parent = _create(client, project_id, f"level {level}", parent)
        ids.append(parent)
    return ids


def test_create_at_the_cap_succeeds_and_one_past_it_is_422(
    client: TestClient,
) -> None:
    project_id = _project_id(client)
    # Growing the chain to the cap asserts a 201 at every level, the deepest
    # included — the cap is reachable, not one short.
    ids = _chain(client, project_id, MAX_SUBTASK_DEPTH)
    assert len(ids) == MAX_SUBTASK_DEPTH

    response = client.post(
        "/api/tasks",
        json={
            "project_id": project_id,
            "title": "one too deep",
            "parent_task_id": ids[-1],
        },
    )
    assert response.status_code == 422, response.text
    assert str(MAX_SUBTASK_DEPTH) in response.json()["detail"]

    # The per-project POST route enforces the same bound as the unscoped one.
    scoped = client.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "one too deep", "parent_task_id": ids[-1]},
    )
    assert scoped.status_code == 422, scoped.text

    # Rejected, not partially committed: the deepest task still has no children.
    subtasks = client.get(f"/api/tasks/{ids[-1]}/subtasks")
    assert subtasks.status_code == 200
    assert subtasks.json() == []


def test_reparent_accounts_for_the_moved_subtree_height(client: TestClient) -> None:
    """A move is judged by the moved subtree's deepest leaf, not just its root."""
    project_id = _project_id(client)
    # Levels 1..(cap - 2); the deepest of these can take two more levels below it.
    trunk = _chain(client, project_id, MAX_SUBTASK_DEPTH - 2)

    # A separate two-level branch (height 1) fits: it would land at cap-1 and cap.
    short_root = _create(client, project_id, "short root", None)
    _create(client, project_id, "short leaf", short_root)
    moved = client.patch(
        f"/api/tasks/{short_root}", json={"parent_task_id": trunk[-1]}
    )
    assert moved.status_code == 200, moved.text

    # A three-level branch (height 2) does not: its leaf would land one past the cap.
    tall_root = _create(client, project_id, "tall root", None)
    tall_mid = _create(client, project_id, "tall mid", tall_root)
    _create(client, project_id, "tall leaf", tall_mid)
    rejected = client.patch(
        f"/api/tasks/{tall_root}", json={"parent_task_id": trunk[-1]}
    )
    assert rejected.status_code == 422, rejected.text

    # The rejected move left the branch where it was.
    still_top_level = client.get(f"/api/tasks/{tall_root}")
    assert still_top_level.status_code == 200
    assert still_top_level.json()["parent_task_id"] is None


def test_reparent_under_a_task_at_the_cap_is_422(client: TestClient) -> None:
    project_id = _project_id(client)
    ids = _chain(client, project_id, MAX_SUBTASK_DEPTH)
    orphan = _create(client, project_id, "leaf looking for a home", None)

    response = client.patch(
        f"/api/tasks/{orphan}", json={"parent_task_id": ids[-1]}
    )
    assert response.status_code == 422, response.text

    # The cap gates *new* nesting only: a PATCH that echoes a task's existing
    # parent back is not a move, and must keep working at (or past) the cap.
    echoed = client.patch(
        f"/api/tasks/{ids[-1]}",
        json={"title": "renamed", "parent_task_id": ids[-2]},
    )
    assert echoed.status_code == 200, echoed.text
    assert echoed.json()["title"] == "renamed"


def test_chain_at_the_cap_survives_the_whole_lifecycle(client: TestClient) -> None:
    """Read, list, delete, restore and purge a chain grown to exactly the cap.

    This is the shape that used to strand rows: creates committed, DELETE 500'd.
    """
    project_id = _project_id(client)
    ids = _chain(client, project_id, MAX_SUBTASK_DEPTH)
    root, leaf = ids[0], ids[-1]

    assert client.get(f"/api/tasks/{root}").status_code == 200
    assert client.get(f"/api/tasks/{leaf}").status_code == 200
    listed = client.get("/api/tasks", params={"limit": 1000})
    assert listed.status_code == 200
    assert client.get(f"/api/projects/{project_id}/tasks").status_code == 200
    assert client.get("/api/dashboard").status_code == 200

    assert client.delete(f"/api/tasks/{root}").status_code == 204
    assert client.get(f"/api/tasks/{leaf}").status_code == 404

    trash = client.get("/api/trash")
    assert trash.status_code == 200

    restored = client.post(
        f"/api/tasks/{root}/restore", params={"restore_subtasks": "true"}
    )
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/tasks/{leaf}").status_code == 200

    assert client.delete(f"/api/tasks/{root}").status_code == 204
    purged = client.post("/api/trash/purge", json={"task_ids": [root]})
    assert purged.status_code == 200, purged.text
    assert purged.json()["tasks"] == MAX_SUBTASK_DEPTH


def test_service_rejects_a_deep_create_directly(db_session: Session) -> None:
    """The bound lives in the service layer, so every write path inherits it.

    The agent's ``create_task`` tool calls the same function the HTTP routes do,
    so it is covered by this check without a second implementation.
    """
    project = projects_service.create_project(db_session, name="Nesting")
    db_session.commit()

    parent_id: int | None = None
    for level in range(MAX_SUBTASK_DEPTH):
        task = tasks_service.create_task(
            db_session,
            project_id=project.id,
            title=f"level {level + 1}",
            parent_task_id=parent_id,
        )
        parent_id = task.id
    db_session.commit()

    with pytest.raises(tasks_service.TaskDepthError):
        tasks_service.create_task(
            db_session,
            project_id=project.id,
            title="one too deep",
            parent_task_id=parent_id,
        )
