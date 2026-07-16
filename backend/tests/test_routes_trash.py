from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.db.models import (
    ActivityEvent,
    Project,
    Task,
    TaskDependency,
)
from app.services.common import soft_delete


def test_trash_empty_by_default(client: TestClient) -> None:
    body = client.get("/api/trash").json()
    assert body == {
        "projects": [],
        "tasks": [],
    }


def test_trash_count_reports_each_kind(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]
    client.delete(f"/api/projects/{pid}")
    tid = client.post("/api/tasks", json={"title": "Pay invoice"}).json()["id"]
    client.delete(f"/api/tasks/{tid}")

    assert client.get("/api/trash/count").json() == {
        "projects": 1,
        "tasks": 1,
    }


def test_trash_count_is_not_capped_by_the_list_page_limit(client: TestClient) -> None:
    # Regression: the nav badge used to count the length of the (page-limited)
    # trash list, so it stuck at the 50-row cap. The count must be exact.
    for i in range(51):
        tid = client.post("/api/tasks", json={"title": f"task {i}"}).json()["id"]
        client.delete(f"/api/tasks/{tid}")

    assert len(client.get("/api/trash").json()["tasks"]) == 50  # list still paginated
    assert client.get("/api/trash/count").json()["tasks"] == 51  # count is exact


def test_project_delete_appears_in_trash_and_restores(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 204

    trash = client.get("/api/trash").json()
    assert [p["id"] for p in trash["projects"]] == [pid]
    assert trash["projects"][0]["deleted_at"] is not None  # trashed row carries it
    assert trash["projects"][0]["archived_task_count"] == 0  # no tasks to bring back
    assert client.get(f"/api/projects/{pid}").status_code == 404  # gone from active

    restored = client.post(f"/api/projects/{pid}/restore")
    assert restored.status_code == 200
    assert restored.json()["project"]["id"] == pid
    assert restored.json()["restored_task_count"] == 0

    active = {p["id"]: p for p in client.get("/api/projects").json()}
    assert pid in active  # active again
    assert active[pid]["deleted_at"] is None  # active row serializes null


def test_project_delete_cascades_tasks_and_restore_brings_them_back(
    client: TestClient,
) -> None:
    pid = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]
    t1 = client.post(f"/api/projects/{pid}/tasks", json={"title": "audit"}).json()["id"]
    client.post(f"/api/projects/{pid}/tasks", json={"title": "patch"})
    assert client.delete(f"/api/projects/{pid}").status_code == 204

    trash = client.get("/api/trash").json()
    # The project advertises its restorable task count; the tasks are NOT listed
    # as standalone trash rows (they belong to the project's restore).
    assert trash["projects"][0]["archived_task_count"] == 2
    assert t1 not in [t["id"] for t in trash["tasks"]]

    # Restore with tasks → they come back active under the project.
    restored = client.post(f"/api/projects/{pid}/restore?restore_tasks=true")
    assert restored.status_code == 200
    assert restored.json()["restored_task_count"] == 2
    titles = {t["title"] for t in client.get(f"/api/projects/{pid}/tasks").json()}
    assert {"audit", "patch"} <= titles


def test_restore_project_without_tasks_leaves_them_trashed(
    client: TestClient,
) -> None:
    pid = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]
    client.post(f"/api/projects/{pid}/tasks", json={"title": "audit"})
    assert client.delete(f"/api/projects/{pid}").status_code == 204

    restored = client.post(f"/api/projects/{pid}/restore")  # restore_tasks defaults false
    assert restored.json()["restored_task_count"] == 0
    assert client.get(f"/api/projects/{pid}/tasks").json() == []


def test_restore_unknown_project_404(client: TestClient) -> None:
    assert client.post("/api/projects/424242/restore").status_code == 404


def test_task_delete_appears_in_trash_and_restores(client: TestClient) -> None:
    tid = client.post("/api/tasks", json={"title": "Pay invoice"}).json()["id"]
    assert client.delete(f"/api/tasks/{tid}").status_code == 204

    trash = client.get("/api/trash").json()
    assert [t["id"] for t in trash["tasks"]] == [tid]
    assert trash["tasks"][0]["deleted_at"] is not None  # trashed row carries it

    restored = client.post(f"/api/tasks/{tid}/restore")
    assert restored.status_code == 200
    assert restored.json()["id"] == tid
    assert client.get("/api/trash").json()["tasks"] == []
    active = client.get(f"/api/tasks/{tid}")
    assert active.status_code == 200
    assert active.json()["deleted_at"] is None  # active row serializes null


# --- Permanent delete / purge (Sprint 9f) ----------------------------------


def test_purge_task_removes_row_and_is_404_on_repeat(client: TestClient) -> None:
    tid = client.post("/api/tasks", json={"title": "Pay invoice"}).json()["id"]
    client.delete(f"/api/tasks/{tid}")

    assert client.delete(f"/api/tasks/{tid}/purge").status_code == 204
    assert client.get("/api/trash").json()["tasks"] == []
    # Gone for good — no longer in trash, so a repeat purge 404s.
    assert client.delete(f"/api/tasks/{tid}/purge").status_code == 404


def test_purge_active_task_409(client: TestClient) -> None:
    tid = client.post("/api/tasks", json={"title": "Still alive"}).json()["id"]
    # Exists but not in trash → 409, never destroyed.
    assert client.delete(f"/api/tasks/{tid}/purge").status_code == 409
    assert client.get(f"/api/tasks/{tid}").status_code == 200


def test_purge_unknown_task_404(client: TestClient) -> None:
    assert client.delete("/api/tasks/424242/purge").status_code == 404


def test_purge_task_cleans_dependency_edges(
    client: TestClient, db_session: Session
) -> None:
    a = client.post("/api/tasks", json={"title": "A"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "B"}).json()["id"]
    # A depends on B, and B depends on A's perspective is irrelevant — one edge.
    client.post(f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b})
    client.delete(f"/api/tasks/{a}")

    assert client.delete(f"/api/tasks/{a}/purge").status_code == 204
    # No dependency row references the purged task on either side.
    edges = db_session.execute(select(TaskDependency)).scalars().all()
    assert all(a not in (e.task_id, e.depends_on_task_id) for e in edges)


def test_purge_parent_task_takes_subtree(
    client: TestClient, db_session: Session
) -> None:
    parent = client.post("/api/tasks", json={"title": "Parent"}).json()["id"]
    child = client.post(
        "/api/tasks", json={"title": "Child", "parent_task_id": parent}
    ).json()["id"]
    client.delete(f"/api/tasks/{parent}")  # cascade-soft-deletes the child too

    assert client.delete(f"/api/tasks/{parent}/purge").status_code == 204
    # Both rows gone; no orphaned child dangling a parent_task_id.
    assert db_session.get(Task, parent) is None
    assert db_session.get(Task, child) is None


def test_purge_project_cleans_all_fk_edges(
    client: TestClient, db_session: Session
) -> None:
    pid = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]
    tid = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "Patch it"}
    ).json()["id"]

    # The project's activity events hold a real FK into projects that purge must clear.
    assert (
        db_session.execute(
            select(ActivityEvent).where(ActivityEvent.project_id == pid)
        ).first()
        is not None
    )

    client.delete(f"/api/projects/{pid}")  # active task rehomes to General
    # Re-point the rehomed task at the trashed project and soft-delete it, so a
    # soft-deleted task still references pid when we purge (the case purge cleans).
    task = db_session.get(Task, tid)
    assert task is not None
    task.project_id = pid
    soft_delete(task)
    db_session.commit()

    assert client.delete(f"/api/projects/{pid}/purge").status_code == 204

    assert db_session.get(Project, pid) is None
    assert db_session.get(Task, tid) is None  # soft-deleted owned task purged
    # Nullable FKs cleared, not dangling; the audit row itself survives.
    assert (
        db_session.execute(
            select(ActivityEvent).where(ActivityEvent.project_id == pid)
        ).first()
        is None
    )


def test_purge_active_project_409(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "Live"}).json()["id"]
    assert client.delete(f"/api/projects/{pid}/purge").status_code == 409


def test_purge_protected_project_403(client: TestClient, db_session: Session) -> None:
    # Filing a task into General materializes the protected project lazily.
    client.post("/api/tasks", json={"title": "files into General"})
    # General can't reach trash through the API, so soft-delete it directly to
    # exercise the guard: purge must refuse a protected project even in trash.
    general = db_session.execute(
        select(Project).where(Project.system_key == "general")
    ).scalar_one()
    soft_delete(general)
    db_session.commit()

    assert client.delete(f"/api/projects/{general.id}/purge").status_code == 403
    assert db_session.get(Project, general.id) is not None  # untouched


def test_empty_trash_clears_all_and_is_idempotent(
    client: TestClient, db_session: Session
) -> None:
    pid = client.post("/api/projects", json={"name": "Doomed"}).json()["id"]
    tid = client.post("/api/tasks", json={"title": "Doomed task"}).json()["id"]
    client.delete(f"/api/projects/{pid}")
    client.delete(f"/api/tasks/{tid}")

    result = client.delete("/api/trash")
    assert result.status_code == 200
    counts = result.json()
    assert counts["projects"] == 1
    assert counts["tasks"] >= 1

    assert client.get("/api/trash").json() == {
        "projects": [],
        "tasks": [],
    }
    # Re-running clears nothing and protected General is spared.
    again = client.delete("/api/trash").json()
    assert again == {
        "projects": 0,
        "tasks": 0,
    }
    assert client.get("/api/projects").json()  # General still present


# --- Bulk purge of a selection (BUG-11) -------------------------------------


def test_purge_selected_parent_and_child_together(
    client: TestClient, db_session: Session
) -> None:
    """The regression: selecting a parent *and* its cascaded child must succeed.

    Purging the parent takes the child's row with it, so by the child's turn it's
    already gone. That's a success, not a failure — both were removed.
    """
    parent = client.post("/api/tasks", json={"title": "Parent"}).json()["id"]
    child = client.post(
        "/api/tasks", json={"title": "Child", "parent_task_id": parent}
    ).json()["id"]
    client.delete(f"/api/tasks/{parent}")  # cascade-soft-deletes the child too

    # Trash lists newest-deleted first, and the cascade stamps the parent last,
    # so the parent genuinely does come first in what the UI sends.
    assert [t["id"] for t in client.get("/api/trash").json()["tasks"]] == [
        parent,
        child,
    ]

    result = client.post(
        "/api/trash/purge", json={"project_ids": [], "task_ids": [parent, child]}
    )
    assert result.status_code == 200
    assert result.json() == {"projects": 0, "tasks": 2}

    assert db_session.get(Task, parent) is None
    assert db_session.get(Task, child) is None
    assert client.get("/api/trash").json()["tasks"] == []


def test_purge_selected_skips_unknown_and_active_ids(client: TestClient) -> None:
    trashed = client.post("/api/tasks", json={"title": "Trashed"}).json()["id"]
    active_task = client.post("/api/tasks", json={"title": "Alive"}).json()["id"]
    client.delete(f"/api/tasks/{trashed}")

    # Ids that were never in trash aren't errors, but don't count as removed.
    result = client.post(
        "/api/trash/purge",
        json={"project_ids": [], "task_ids": [trashed, active_task, 424242]},
    )
    assert result.status_code == 200
    assert result.json() == {"projects": 0, "tasks": 1}
    assert client.get(f"/api/tasks/{active_task}").status_code == 200  # untouched


def test_purge_selected_empty_selection_is_a_noop(client: TestClient) -> None:
    tid = client.post("/api/tasks", json={"title": "Survivor"}).json()["id"]
    client.delete(f"/api/tasks/{tid}")

    result = client.post(
        "/api/trash/purge", json={"project_ids": [], "task_ids": []}
    )
    assert result.status_code == 200
    assert result.json() == {"projects": 0, "tasks": 0}
    # An empty selection must not be read as "everything".
    assert [t["id"] for t in client.get("/api/trash").json()["tasks"]] == [tid]


def test_purge_selected_spares_protected_project(
    client: TestClient, db_session: Session
) -> None:
    client.post("/api/tasks", json={"title": "files into General"})
    general = db_session.execute(
        select(Project).where(Project.system_key == "general")
    ).scalar_one()
    soft_delete(general)
    db_session.commit()

    result = client.post(
        "/api/trash/purge", json={"project_ids": [general.id], "task_ids": []}
    )
    assert result.status_code == 200
    assert result.json()["projects"] == 0
    assert db_session.get(Project, general.id) is not None  # untouched


def test_purge_selected_removes_projects(
    client: TestClient, db_session: Session
) -> None:
    pid = client.post("/api/projects", json={"name": "Doomed"}).json()["id"]
    client.delete(f"/api/projects/{pid}")

    result = client.post(
        "/api/trash/purge", json={"project_ids": [pid], "task_ids": []}
    )
    assert result.status_code == 200
    assert result.json()["projects"] == 1
    assert db_session.get(Project, pid) is None


# --- LAN clients may purge --------------------------------------------------
#
# Purge/empty-trash are the app's only irreversible deletes, but the trusted
# single-user LAN reaches the whole UI through a gateway, so they must be
# usable from a LAN client too (the loopback-only guard was removed).


@pytest.fixture
def lan_client(client: TestClient) -> Generator[TestClient, None, None]:
    """A client presenting a LAN address instead of loopback/testclient.

    Depends on ``client`` so the get_db override (and its shared session) is
    already installed — both clients hit the same database.
    """
    with TestClient(app, client=("192.168.1.50", 50000)) as test_client:
        yield test_client


def test_lan_client_can_empty_trash(
    client: TestClient, lan_client: TestClient
) -> None:
    tid = client.post("/api/tasks", json={"title": "LAN task"}).json()["id"]
    client.delete(f"/api/tasks/{tid}")

    resp = lan_client.delete("/api/trash")
    assert resp.status_code == 200
    assert client.get("/api/trash").json()["tasks"] == []


def test_lan_client_can_purge_and_restore(
    client: TestClient, lan_client: TestClient
) -> None:
    tid = client.post("/api/tasks", json={"title": "LAN-managed task"}).json()["id"]

    # Reversible operations stay open to LAN clients (that's the normal app flow)…
    assert lan_client.delete(f"/api/tasks/{tid}").status_code == 204
    assert lan_client.post(f"/api/tasks/{tid}/restore").status_code == 200
    # …and so is the irreversible purge, now that the loopback guard is gone.
    assert lan_client.delete(f"/api/tasks/{tid}").status_code == 204
    assert lan_client.delete(f"/api/tasks/{tid}/purge").status_code == 204


def test_lan_client_can_purge_project(
    client: TestClient, lan_client: TestClient
) -> None:
    pid = client.post("/api/projects", json={"name": "Kept"}).json()["id"]
    client.delete(f"/api/projects/{pid}")

    assert lan_client.delete(f"/api/projects/{pid}/purge").status_code == 204
    # Destroyed: trash is empty for loopback too.
    trash = client.get("/api/trash").json()
    assert [p["id"] for p in trash["projects"]] == []
