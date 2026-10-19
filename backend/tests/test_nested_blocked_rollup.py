"""A subtask held open by its own blocker holds every ancestor open (#312).

Roll-up used to combine children's *uncapped* statuses and apply the dependency
cap afterwards, only to each task's own edges. A checklist P > C > G where C
waits on an independent B therefore read P ``done`` the moment G finished —
while C still read ``in_progress`` — and, P being recurring, spawned P's next
occurrence early. Roll-up and cap are now one computation; every surface below
must agree that P is not done until B is.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import TaskDependency, TaskWorkflowStatus
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service


def _create(client: TestClient, title: str, parent: int | None = None) -> int:
    body: dict[str, Any] = {"title": title}
    if parent is not None:
        body["parent_task_id"] = parent
    resp = client.post("/api/tasks", json=body)
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


def _status(client: TestClient, task_id: int) -> str:
    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200, resp.text
    return str(resp.json()["workflow_status"])


def _ids(client: TestClient, query: str = "") -> set[int]:
    resp = client.get(f"/api/tasks{query}")
    assert resp.status_code == 200, resp.text
    return {int(row["id"]) for row in resp.json()}


def _series_due_dates(client: TestClient, task_id: int) -> list[str]:
    resp = client.get(f"/api/tasks/{task_id}/series")
    assert resp.status_code == 200, resp.text
    return [str(row["due_date"]) for row in resp.json()["occurrences"]]


def _dashboard_counts(client: TestClient) -> tuple[int, int]:
    """(total open, total done) as the dashboard reports them."""
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    done = sum(int(row["done_task_count"]) for row in data["projects"])
    return int(data["total_open_tasks"]), done


def test_nested_blocked_subtask_holds_recurring_parent_open(
    client: TestClient, db_session: Session
) -> None:
    p = _create(client, "P")
    c = _create(client, "C", parent=p)
    g = _create(client, "G", parent=c)
    b = _create(client, "B")
    resp = client.patch(
        f"/api/tasks/{p}",
        json={"due_date": "2026-09-23", "repeat_interval": {"unit": "day", "every": 1}},
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/tasks/{c}/dependencies", json={"depends_on_task_id": b})
    assert resp.status_code in (200, 201), resp.text

    resp = client.post(f"/api/tasks/{g}/done")
    assert resp.status_code == 200, resp.text

    # Task reads: G is done, but C is capped by B and P rolls up from C.
    assert _status(client, g) == "done"
    assert _status(client, c) == "in_progress"
    assert _status(client, p) == "in_progress"
    assert _status(client, b) == "open"
    p_read = client.get(f"/api/tasks/{p}").json()
    assert p_read["subtask_status"] == "in_progress"
    assert p_read["next_occurrence_date"] == "2026-09-24"

    # Effective statuses (the dependency-check authority) agree.
    statuses = deps_service.effective_statuses(db_session, [p, c, g, b])
    assert statuses[p] == TaskWorkflowStatus.in_progress
    assert statuses[c] == TaskWorkflowStatus.in_progress
    assert statuses[g] == TaskWorkflowStatus.done

    # Done/open list membership.
    open_ids = _ids(client)
    done_ids = _ids(client, "?workflow_status=done")
    assert {p, c, b} <= open_ids
    assert p not in done_ids and c not in done_ids
    assert g in done_ids
    assert {t.id for t in tasks_service.list_tasks(db_session, exclude_done=True)} >= {
        p,
        c,
        b,
    }
    assert _dashboard_counts(client) == (3, 1)  # P, C, B open; only G done

    # Recurrence: no successor yet.
    assert _series_due_dates(client, p) == ["2026-09-23"]

    # Completing the blocker completes C, then P, and spawns exactly one successor.
    resp = client.post(f"/api/tasks/{b}/done")
    assert resp.status_code == 200, resp.text

    assert _status(client, c) == "done"
    assert _status(client, p) == "done"
    assert deps_service.effective_statuses(db_session, [p])[p] == (
        TaskWorkflowStatus.done
    )
    assert p in _ids(client, "?workflow_status=done")
    assert p not in _ids(client)
    assert _series_due_dates(client, p) == ["2026-09-23", "2026-09-24"]
    # Old P, C, G, B done; the successor's fresh checklist (P', C', G') is open.
    assert _dashboard_counts(client) == (3, 4)
    assert client.get(f"/api/tasks/{p}").json()["next_occurrence_date"] is None


def test_parent_of_blocked_done_leaf_is_not_done(db_session: Session) -> None:
    """The leaf case: a stored-done grandchild re-blocked by a restored blocker.

    A leaf can be stored ``done`` yet blocked (completed while its blocker sat in
    the trash). Its capped ``in_progress`` must reach its ancestors as well.
    """
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    leaf = tasks_service.create_task(
        db_session, project_id=None, title="leaf", parent_task_id=parent.id
    )
    blocker = tasks_service.create_task(db_session, project_id=None, title="blocker")
    deps_service.add_dependency(db_session, leaf.id, blocker.id)
    db_session.commit()

    tasks_service.soft_delete_task(db_session, blocker)
    db_session.commit()
    tasks_service.mark_done(db_session, leaf)
    db_session.commit()
    rollup = tasks_service.get_rollup(db_session, parent)
    assert rollup.workflow_status == TaskWorkflowStatus.done

    # Undo the trash directly (the restore path is out of scope here): the
    # blocker is live and open again, so the leaf and its parent are held open.
    blocker.deleted_at = None
    db_session.commit()
    rollups = tasks_service.compute_rollups(db_session, [parent, leaf])
    assert rollups[leaf.id].is_blocked is True
    assert rollups[leaf.id].workflow_status == TaskWorkflowStatus.in_progress
    assert rollups[parent.id].workflow_status == TaskWorkflowStatus.in_progress
    assert rollups[parent.id].is_blocked is False

    full = tasks_service.compute_rollups_for_full_set(
        db_session, tasks_service.list_tasks(db_session)
    )
    assert full[parent.id].workflow_status == TaskWorkflowStatus.in_progress


def test_subtask_waiting_on_its_own_ancestor_terminates(db_session: Session) -> None:
    """A pre-existing mixed parent/dependency cycle resolves, never done.

    ``add_dependency`` rejects this edge now (#354), so it is inserted directly,
    standing in for data written before the guard.
    """
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    child = tasks_service.create_task(
        db_session, project_id=None, title="child", parent_task_id=parent.id
    )
    db_session.add(TaskDependency(task_id=child.id, depends_on_task_id=parent.id))
    db_session.commit()

    for order in ([parent, child], [child, parent]):
        rollups = tasks_service.compute_rollups(db_session, order)
        assert rollups[child.id].is_blocked is True
        assert rollups[parent.id].workflow_status != TaskWorkflowStatus.done
