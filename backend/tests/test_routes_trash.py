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
        "purge_total": 2,  # 1 project + 1 standalone task, all removable
    }


def test_trash_count_is_not_capped_by_the_list_page_limit(client: TestClient) -> None:
    # Regression: the nav badge used to count the length of the (page-limited)
    # trash list, so it stuck at the 50-row cap. The count must be exact.
    for i in range(51):
        tid = client.post("/api/tasks", json={"title": f"task {i}"}).json()["id"]
        client.delete(f"/api/tasks/{tid}")

    assert len(client.get("/api/trash").json()["tasks"]) == 50  # list still paginated
    assert client.get("/api/trash/count").json()["tasks"] == 51  # count is exact


def test_trash_count_purge_total_matches_what_empty_trash_removes(
    client: TestClient, db_session: Session
) -> None:
    # purge_total drives the Empty-trash confirm, so it must equal exactly what
    # DELETE /api/trash removes: all soft-deleted tasks (INCLUDING those cascaded
    # with a project, which the badge's `tasks` excludes) plus non-protected
    # projects. A cascade project (2 archived tasks) + a standalone task → the
    # badge shows projects=1, tasks=1, but purge_total is 1 project + 3 tasks = 4.
    pid = client.post("/api/projects", json={"name": "Doomed"}).json()["id"]
    client.post(f"/api/projects/{pid}/tasks", json={"title": "cascade a"})
    client.post(f"/api/projects/{pid}/tasks", json={"title": "cascade b"})
    standalone = client.post("/api/tasks", json={"title": "standalone"}).json()["id"]
    client.delete(f"/api/projects/{pid}")  # cascades its 2 tasks into trash
    client.delete(f"/api/tasks/{standalone}")

    count = client.get("/api/trash/count").json()
    assert count["projects"] == 1
    assert count["tasks"] == 1  # standalone only; cascade tasks excluded from badge
    assert count["purge_total"] == 4  # 1 project + 3 tasks (2 cascade + 1 standalone)

    # Emptying trash removes exactly purge_total rows.
    removed = client.delete("/api/trash").json()
    assert removed["projects"] + removed["tasks"] == count["purge_total"]


def test_trash_count_purge_total_excludes_protected_projects(
    client: TestClient, db_session: Session
) -> None:
    # empty_trash spares the protected General project, so purge_total must not
    # count it — otherwise the confirm would overstate the deletion.
    tid = client.post("/api/tasks", json={"title": "files into General"}).json()["id"]
    client.delete(f"/api/tasks/{tid}")  # a real standalone trash row
    general = db_session.execute(
        select(Project).where(Project.system_key == "general")
    ).scalar_one()
    soft_delete(general)  # General can't reach trash via the API; force it here
    db_session.commit()

    count = client.get("/api/trash/count").json()
    assert count["projects"] == 1  # badge counts every deleted project, protected too
    # Only the standalone task is purgeable; General (protected) is spared, so
    # purge_total is 1 rather than 2.
    assert count["purge_total"] == 1


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
    tid = client.post(f"/api/projects/{pid}/tasks", json={"title": "audit"}).json()[
        "id"
    ]
    assert client.delete(f"/api/projects/{pid}").status_code == 204

    restored = client.post(f"/api/projects/{pid}/restore")  # restore_tasks defaults false
    assert restored.json()["restored_task_count"] == 0
    # The task is still trashed (not active under the project)...
    assert client.get(f"/api/projects/{pid}/tasks").json() == []
    # ...but no longer stranded: it now shows in the standalone Tasks trash and is
    # individually restorable back into the now-active project.
    trash = client.get("/api/trash").json()
    assert tid in [t["id"] for t in trash["tasks"]]
    restored_task = client.post(f"/api/tasks/{tid}/restore")
    assert restored_task.status_code == 200
    assert restored_task.json()["project_id"] == pid


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


# --- Purge lands in the audit trail (#170) ----------------------------------


def _actions_for(db: Session, entity_type: str, entity_id: int) -> list[str]:
    """Every recorded action for one entity, oldest first.

    Queried by ``entity_type``/``entity_id`` rather than ``project_id`` on purpose:
    purge nulls the project FK, so the entity coordinates are the only handle left
    on a destroyed row's history.
    """
    return [
        e.action
        for e in db.execute(
            select(ActivityEvent)
            .where(
                ActivityEvent.entity_type == entity_type,
                ActivityEvent.entity_id == entity_id,
            )
            .order_by(ActivityEvent.id)
        ).scalars()
    ]


def test_soft_delete_and_purge_are_distinguishable_in_the_audit_log(
    client: TestClient, db_session: Session
) -> None:
    """The regression: a purged task's last event used to be "deleted".

    That is indistinguishable from a task still sitting restorable in trash, so
    the audit log couldn't answer "was this destroyed?".
    """
    pid = client.post("/api/projects", json={"name": "Firewall"}).json()["id"]
    tid = client.post(f"/api/projects/{pid}/tasks", json={"title": "Patch"}).json()[
        "id"
    ]

    client.delete(f"/api/tasks/{tid}")
    assert _actions_for(db_session, "task", tid) == ["created", "deleted"]

    assert client.delete(f"/api/tasks/{tid}/purge").status_code == 204
    assert db_session.get(Task, tid) is None
    assert _actions_for(db_session, "task", tid) == ["created", "deleted", "purged"]


def test_purge_event_snapshots_the_title_and_keeps_actor_attribution(
    client: TestClient, db_session: Session
) -> None:
    tid = client.post("/api/tasks", json={"title": "Rotate keys"}).json()["id"]
    client.delete(f"/api/tasks/{tid}")
    client.delete(f"/api/tasks/{tid}/purge")

    event = db_session.execute(
        select(ActivityEvent).where(
            ActivityEvent.entity_type == "task",
            ActivityEvent.entity_id == tid,
            ActivityEvent.action == "purged",
        )
    ).scalar_one()
    # The title lives on only in the summary once the row is gone.
    assert "Rotate keys" in event.summary
    # Requests from the UI/API are the user: actor stays NULL, same as every
    # other user-driven event.
    assert event.actor is None


def test_agent_actor_is_preserved_on_purge(db_session: Session) -> None:
    from app.services import activity, task_trash, tasks

    task = tasks.create_task(db_session, project_id=None, title="Agent's doing")
    soft_delete(task)
    db_session.flush()

    token = activity.current_actor.set("agent:mcp")
    try:
        task_trash.purge_task(db_session, task)
    finally:
        activity.current_actor.reset(token)
    db_session.commit()

    event = db_session.execute(
        select(ActivityEvent).where(
            ActivityEvent.entity_type == "task", ActivityEvent.action == "purged"
        )
    ).scalar_one()
    assert event.actor == "agent:mcp"


def test_purging_a_parent_audits_every_cascaded_subtree_task(
    client: TestClient, db_session: Session
) -> None:
    parent = client.post("/api/tasks", json={"title": "Parent"}).json()["id"]
    child = client.post(
        "/api/tasks", json={"title": "Child", "parent_task_id": parent}
    ).json()["id"]
    client.delete(f"/api/tasks/{parent}")

    assert client.delete(f"/api/tasks/{parent}/purge").status_code == 204
    # The child was destroyed by the cascade, so it needs its own purge event —
    # nothing else records that it ceased to exist.
    assert _actions_for(db_session, "task", parent)[-1] == "purged"
    assert _actions_for(db_session, "task", child)[-1] == "purged"


def test_purging_a_project_audits_the_project_and_its_owned_tasks(
    client: TestClient, db_session: Session
) -> None:
    pid = client.post("/api/projects", json={"name": "Doomed"}).json()["id"]
    tid = client.post(f"/api/projects/{pid}/tasks", json={"title": "Owned"}).json()[
        "id"
    ]
    client.delete(f"/api/projects/{pid}")
    # Re-file the rehomed task under the trashed project so purge owns it.
    task = db_session.get(Task, tid)
    assert task is not None
    task.project_id = pid
    soft_delete(task)
    db_session.commit()

    assert client.delete(f"/api/projects/{pid}/purge").status_code == 204

    assert _actions_for(db_session, "project", pid)[-1] == "purged"
    assert _actions_for(db_session, "task", tid)[-1] == "purged"
    # The FK into a destroyed project cannot survive, so the purge event points at
    # the project through entity_id only; project_id is nulled with the rest of
    # the project's history.
    purge_event = db_session.execute(
        select(ActivityEvent).where(
            ActivityEvent.entity_type == "project",
            ActivityEvent.entity_id == pid,
            ActivityEvent.action == "purged",
        )
    ).scalar_one()
    assert purge_event.project_id is None
    assert "Doomed" in purge_event.summary


def test_empty_trash_audits_every_row_it_removes(
    client: TestClient, db_session: Session
) -> None:
    pid = client.post("/api/projects", json={"name": "Doomed"}).json()["id"]
    cascade = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "Cascade"}
    ).json()["id"]
    standalone = client.post("/api/tasks", json={"title": "Standalone"}).json()["id"]
    client.delete(f"/api/projects/{pid}")  # cascade-soft-deletes its task
    client.delete(f"/api/tasks/{standalone}")

    counts = client.delete("/api/trash").json()
    assert counts == {"projects": 1, "tasks": 2}

    purged = [
        (e.entity_type, e.entity_id)
        for e in db_session.execute(
            select(ActivityEvent).where(ActivityEvent.action == "purged")
        ).scalars()
    ]
    # One event per removed row, and no more: the counts and the audit agree.
    assert sorted(purged) == sorted(
        [("project", pid), ("task", cascade), ("task", standalone)]
    )


def test_purge_selected_audits_only_the_rows_it_actually_removes(
    client: TestClient, db_session: Session
) -> None:
    doomed = client.post("/api/tasks", json={"title": "Doomed"}).json()["id"]
    spared = client.post("/api/tasks", json={"title": "Spared"}).json()["id"]
    client.delete(f"/api/tasks/{doomed}")
    client.delete(f"/api/tasks/{spared}")

    resp = client.post(
        "/api/trash/purge", json={"project_ids": [], "task_ids": [doomed, 999999]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"projects": 0, "tasks": 1}

    assert _actions_for(db_session, "task", doomed)[-1] == "purged"
    assert _actions_for(db_session, "task", spared) == ["created", "deleted"]
