"""Undoing a Trash restore reverses exactly what that restore changed (#306, #307).

The mobile Trash page offers a five-second Undo after each restore. It used to
send an ordinary cascading ``DELETE`` for the id it had clicked, which is not the
inverse of a restore:

* **#307** — restoring a parent task restores the parent *only*, but ``DELETE``
  cascades through every active descendant, so a child the user had restored
  individually beforehand was re-trashed by the Undo.
* **#306** — restoring a *skipped* recurring occurrence rewinds the live
  successor onto the skipped date and purges the original row, returning the
  successor under a different id. ``DELETE`` of the original id 404'd and the
  series stayed rewound (and deleting the successor would trash the series'
  live occurrence instead of reversing the rewind).

The Trash restore routes now hand back an ``undo`` receipt describing what the
restore did, and ``POST /api/trash/{tasks,projects}/undo-restore`` replays it
backwards.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, Task

DAILY = {"repeat_interval": {"unit": "day", "every": 1}}


def _task(client: TestClient, **fields: Any) -> dict[str, Any]:
    resp = client.post("/api/tasks", json=fields)
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _restore(client: TestClient, task_id: int) -> dict[str, Any]:
    resp = client.post(f"/api/trash/tasks/{task_id}/restore")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _trashed_task_ids(client: TestClient) -> set[int]:
    return {t["id"] for t in client.get("/api/trash?limit=200").json()["tasks"]}


# --- #307: a parent restore's undo leaves an earlier-restored child alone --------


def test_undo_parent_restore_keeps_a_child_restored_before_it(
    client: TestClient,
) -> None:
    parent = _task(client, title="P")
    child = _task(client, title="C", parent_task_id=parent["id"])
    assert client.delete(f"/api/tasks/{parent['id']}").status_code == 204

    _restore(client, child["id"])  # C is back first, on its own terms
    result = _restore(client, parent["id"])
    assert result["task"]["id"] == parent["id"]

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 204, undo.text

    assert client.get(f"/api/tasks/{parent['id']}").status_code == 404
    assert client.get(f"/api/tasks/{child['id']}").status_code == 200
    trashed = _trashed_task_ids(client)
    assert parent["id"] in trashed
    assert child["id"] not in trashed


def test_undo_parent_restore_keeps_a_cross_project_child(
    client: TestClient,
) -> None:
    other = client.post("/api/projects", json={"name": "Elsewhere"}).json()
    parent = _task(client, title="P")
    child = _task(
        client, title="C", parent_task_id=parent["id"], project_id=other["id"]
    )
    assert client.delete(f"/api/tasks/{parent['id']}").status_code == 204
    _restore(client, child["id"])
    result = _restore(client, parent["id"])

    assert (
        client.post("/api/trash/tasks/undo-restore", json=result["undo"]).status_code
        == 204
    )
    assert client.get(f"/api/tasks/{parent['id']}").status_code == 404
    live_child = client.get(f"/api/tasks/{child['id']}")
    assert live_child.status_code == 200
    assert live_child.json()["project_id"] == other["id"]


def test_undo_restore_keeps_the_cascade_marker_so_subtree_restore_still_works(
    client: TestClient,
) -> None:
    grand = _task(client, title="G")
    parent = _task(client, title="P", parent_task_id=grand["id"])
    assert client.delete(f"/api/tasks/{grand['id']}").status_code == 204

    result = _restore(client, parent["id"])
    assert (
        client.post("/api/trash/tasks/undo-restore", json=result["undo"]).status_code
        == 204
    )
    # P went back as part of G's cascade, so restoring G with its subtree
    # brings P back exactly as it would have before the restore/undo pair.
    resp = client.post(f"/api/tasks/{grand['id']}/restore?restore_subtasks=true")
    assert resp.status_code == 200
    assert client.get(f"/api/tasks/{parent['id']}").status_code == 200


def test_undo_a_restore_that_is_no_longer_in_effect_is_a_conflict(
    client: TestClient,
) -> None:
    task = _task(client, title="Gone again")
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    result = _restore(client, task["id"])
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 409


def test_undo_restore_is_audited(client: TestClient, db_session: Session) -> None:
    task = _task(client, title="Audited")
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    result = _restore(client, task["id"])
    assert (
        client.post("/api/trash/tasks/undo-restore", json=result["undo"]).status_code
        == 204
    )
    actions = [
        e.action
        for e in db_session.execute(
            select(ActivityEvent)
            .where(
                ActivityEvent.entity_type == "task",
                ActivityEvent.entity_id == task["id"],
            )
            .order_by(ActivityEvent.id)
        ).scalars()
    ]
    assert actions[-2:] == ["restored", "deleted"]


# --- #306: undoing an un-skip returns the series to its pre-restore state ----------


def _checklist_series(client: TestClient) -> tuple[dict[str, Any], list[int]]:
    """A daily checklist occurrence due 2026-09-23 with two steps."""
    root = _task(client, title="Routine", due_date="2026-09-23")
    steps = [
        _task(client, title=title, parent_task_id=root["id"], due_date="2026-09-23")[
            "id"
        ]
        for title in ("Step A", "Step B")
    ]
    resp = client.patch(f"/api/tasks/{root['id']}", json=DAILY)
    assert resp.status_code == 200, resp.text
    return root, steps


def _subtasks(client: TestClient, task_id: int) -> list[dict[str, Any]]:
    resp = client.get(f"/api/tasks/{task_id}/subtasks")
    assert resp.status_code == 200
    body: list[dict[str, Any]] = resp.json()
    return body


def test_undo_unskip_returns_the_series_to_its_pre_restore_state(
    client: TestClient, db_session: Session
) -> None:
    root, _ = _checklist_series(client)
    skip = client.post(f"/api/tasks/{root['id']}/skip")
    assert skip.status_code == 200
    successor = skip.json()
    assert successor["due_date"] == "2026-09-24"
    successor_steps = _subtasks(client, successor["id"])
    assert len(successor_steps) == 2
    # Progress on the live occurrence must survive the restore/undo round trip.
    done = client.post(f"/api/tasks/{successor_steps[0]['id']}/done")
    assert done.status_code == 200

    result = _restore(client, root["id"])
    # The un-skip rewinds the successor and hands it back under its own id.
    assert result["task"]["id"] == successor["id"]
    assert result["task"]["due_date"] == "2026-09-23"

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 204, undo.text

    # The live occurrence is back on its later date, same id, checklist intact.
    live = client.get(f"/api/tasks/{successor['id']}").json()
    assert live["due_date"] == "2026-09-24"
    steps_after = _subtasks(client, successor["id"])
    assert [s["id"] for s in steps_after] == [s["id"] for s in successor_steps]
    assert {s["due_date"] for s in steps_after} == {"2026-09-24"}
    assert steps_after[0]["workflow_status"] == "done"

    # Exactly one live occurrence in the series, and a skipped 09-23 in trash.
    series = client.get(f"/api/tasks/{successor['id']}/series").json()
    occurrences = series["occurrences"]
    live_rows = [o for o in occurrences if o["deleted_at"] is None]
    assert [o["id"] for o in live_rows] == [successor["id"]]
    skipped = db_session.execute(
        select(Task).where(
            Task.recurrence_id == live["recurrence_id"],
            Task.skipped_at.is_not(None),
        )
    ).scalars().all()
    assert [t.due_date.isoformat() for t in skipped if t.due_date] == ["2026-09-23"]
    tombstone = skipped[0]
    assert tombstone.deleted_at is not None
    assert tombstone.id in _trashed_task_ids(client)
    # The skipped occurrence carries its checklist into the trash as one unit.
    tombstone_steps = db_session.execute(
        select(Task).where(Task.parent_task_id == tombstone.id)
    ).scalars().all()
    assert sorted(t.title for t in tombstone_steps) == ["Step A", "Step B"]
    assert all(t.deleted_with_task_id == tombstone.id for t in tombstone_steps)

    # And the un-skip is repeatable from there: restoring the skipped row again
    # rewinds the same live occurrence.
    again = _restore(client, tombstone.id)
    assert again["task"]["id"] == successor["id"]
    assert again["task"]["due_date"] == "2026-09-23"


def test_undo_unskip_after_the_occurrence_moved_is_a_conflict(
    client: TestClient,
) -> None:
    root = _task(client, title="Daily", due_date="2026-09-23")
    assert client.patch(f"/api/tasks/{root['id']}", json=DAILY).status_code == 200
    successor = client.post(f"/api/tasks/{root['id']}/skip").json()
    result = _restore(client, root["id"])
    # The user edits the rewound occurrence's date inside the Undo window.
    moved = client.patch(
        f"/api/tasks/{successor['id']}", json={"due_date": "2026-09-30"}
    )
    assert moved.status_code == 200

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 409
    assert client.get(f"/api/tasks/{successor['id']}").json()["due_date"] == (
        "2026-09-30"
    )


def test_undo_in_place_unskip_puts_the_occurrence_back_as_skipped(
    client: TestClient, db_session: Session
) -> None:
    root, steps = _checklist_series(client)
    successor = client.post(f"/api/tasks/{root['id']}/skip").json()
    # With no live successor the un-skip restores the original row in place,
    # checklist included (issue #241).
    assert client.delete(f"/api/tasks/{successor['id']}").status_code == 204
    result = _restore(client, root["id"])
    assert result["task"]["id"] == root["id"]
    assert len(_subtasks(client, root["id"])) == 2

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 204, undo.text

    db_session.expire_all()
    row = db_session.get(Task, root["id"])
    assert row is not None
    assert row.deleted_at is not None
    assert row.skipped_at is not None
    for step_id in steps:
        step = db_session.get(Task, step_id)
        assert step is not None
        assert step.deleted_at is not None
        assert step.deleted_with_task_id == root["id"]


# --- Projects: undo reverses the restore, not an ordinary project delete --------


def test_undo_project_restore_without_tasks_puts_the_tasks_back_under_it(
    client: TestClient,
) -> None:
    pid = client.post("/api/projects", json={"name": "Shelved"}).json()["id"]
    client.post(f"/api/projects/{pid}/tasks", json={"title": "a"})
    client.post(f"/api/projects/{pid}/tasks", json={"title": "b"})
    assert client.delete(f"/api/projects/{pid}").status_code == 204

    resp = client.post(f"/api/projects/{pid}/restore")
    assert resp.status_code == 200
    body = resp.json()
    # Restoring without tasks moved them to the standalone Tasks trash.
    assert len(_trashed_task_ids(client)) == 2

    undo = client.post("/api/trash/projects/undo-restore", json=body["undo"])
    assert undo.status_code == 204, undo.text

    trash = client.get("/api/trash").json()
    assert [p["id"] for p in trash["projects"]] == [pid]
    assert trash["projects"][0]["archived_task_count"] == 2
    assert trash["tasks"] == []


def test_undo_project_restore_with_tasks_re_archives_exactly_those_tasks(
    client: TestClient,
) -> None:
    pid = client.post("/api/projects", json={"name": "Shelved"}).json()["id"]
    client.post(f"/api/projects/{pid}/tasks", json={"title": "a"})
    assert client.delete(f"/api/projects/{pid}").status_code == 204

    body = client.post(f"/api/projects/{pid}/restore?restore_tasks=true").json()
    assert body["restored_task_count"] == 1
    assert (
        client.post("/api/trash/projects/undo-restore", json=body["undo"]).status_code
        == 204
    )
    trash = client.get("/api/trash").json()
    assert trash["projects"][0]["archived_task_count"] == 1


def test_undo_project_restore_refuses_to_strand_work_added_since(
    client: TestClient,
) -> None:
    pid = client.post("/api/projects", json={"name": "Shelved"}).json()["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    body = client.post(f"/api/projects/{pid}/restore").json()
    fresh = client.post(f"/api/projects/{pid}/tasks", json={"title": "new work"})
    assert fresh.status_code == 201

    undo = client.post("/api/trash/projects/undo-restore", json=body["undo"])
    assert undo.status_code == 409
    assert client.get(f"/api/tasks/{fresh.json()['id']}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 200
