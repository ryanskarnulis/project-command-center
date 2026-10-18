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


# --- #313: a receipt is bound to its own restore and refuses once rows moved on ----


def _task_event_count(db_session: Session) -> int:
    return len(
        db_session.execute(
            select(ActivityEvent.id).where(ActivityEvent.entity_type == "task")
        ).all()
    )


def test_undo_restore_after_the_task_was_edited_is_a_conflict(
    client: TestClient, db_session: Session
) -> None:
    task = _task(client, title="Original")
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    result = _restore(client, task["id"])
    edited = client.patch(
        f"/api/tasks/{task['id']}", json={"title": "Edited since restore"}
    )
    assert edited.status_code == 200
    events_before = _task_event_count(db_session)

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 409, undo.text

    # Nothing written: the edited task stays active and unaudited by the undo.
    live = client.get(f"/api/tasks/{task['id']}")
    assert live.status_code == 200
    assert live.json()["title"] == "Edited since restore"
    assert task["id"] not in _trashed_task_ids(client)
    assert _task_event_count(db_session) == events_before


def test_undo_restore_after_a_restored_checklist_row_changed_is_a_conflict(
    client: TestClient,
) -> None:
    root, steps = _checklist_series(client)
    successor = client.post(f"/api/tasks/{root['id']}/skip").json()
    assert client.delete(f"/api/tasks/{successor['id']}").status_code == 204
    result = _restore(client, root["id"])  # in-place un-skip brings the steps back
    assert set(result["undo"]["restored_task_ids"]) == {root["id"], *steps}
    assert client.post(f"/api/tasks/{steps[0]}/done").status_code == 200

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 409
    assert client.get(f"/api/tasks/{root['id']}").status_code == 200
    assert client.get(f"/api/tasks/{steps[0]}").json()["workflow_status"] == "done"


def test_an_old_receipt_stays_dead_after_a_newer_restore(client: TestClient) -> None:
    task = _task(client, title="Cycled")
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    first = _restore(client, task["id"])
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    second = _restore(client, task["id"])
    # Same ids, same row, active again — but the first receipt belongs to a
    # restore that is no longer the one in effect.
    assert first["undo"]["restored_task_ids"] == second["undo"]["restored_task_ids"]

    stale = client.post("/api/trash/tasks/undo-restore", json=first["undo"])
    assert stale.status_code == 409
    assert client.get(f"/api/tasks/{task['id']}").status_code == 200

    fresh = client.post("/api/trash/tasks/undo-restore", json=second["undo"])
    assert fresh.status_code == 204, fresh.text
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_undo_restore_ignores_changes_to_rows_it_did_not_restore(
    client: TestClient,
) -> None:
    project = client.post("/api/projects", json={"name": "Home"}).json()
    task = _task(client, title="Mine", project_id=project["id"])
    neighbour = _task(client, title="Neighbour", project_id=project["id"])
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    result = _restore(client, task["id"])

    # Another task and the owning project change inside the Undo window.
    assert (
        client.patch(
            f"/api/tasks/{neighbour['id']}", json={"title": "Neighbour edited"}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/projects/{project['id']}", json={"name": "Home renamed"}
        ).status_code
        == 200
    )

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 204, undo.text
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404
    assert client.get(f"/api/tasks/{neighbour['id']}").status_code == 200


def test_undo_restore_rejects_a_marker_from_another_event(
    client: TestClient, db_session: Session
) -> None:
    task = _task(client, title="Forged")
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    result = _restore(client, task["id"])
    created = db_session.execute(
        select(ActivityEvent.id).where(
            ActivityEvent.entity_type == "task",
            ActivityEvent.entity_id == task["id"],
            ActivityEvent.action == "created",
        )
    ).scalar_one()
    forged = {**result["undo"], "restore_event_id": created}

    undo = client.post("/api/trash/tasks/undo-restore", json=forged)
    assert undo.status_code == 409
    assert client.get(f"/api/tasks/{task['id']}").status_code == 200


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


def test_an_old_project_receipt_stays_dead_after_a_newer_restore(
    client: TestClient,
) -> None:
    pid = client.post("/api/projects", json={"name": "Shelved"}).json()["id"]
    client.post(f"/api/projects/{pid}/tasks", json={"title": "a"})
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    first = client.post(f"/api/projects/{pid}/restore").json()["undo"]
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    second = client.post(f"/api/projects/{pid}/restore").json()["undo"]
    assert second["restore_event_id"] > first["restore_event_id"]

    stale = client.post("/api/trash/projects/undo-restore", json=first)
    assert stale.status_code == 409
    assert client.get(f"/api/projects/{pid}").status_code == 200
    assert (
        client.post("/api/trash/projects/undo-restore", json=second).status_code
        == 204
    )


def test_undo_project_restore_rejects_a_marker_from_another_event(
    client: TestClient,
) -> None:
    pid = client.post("/api/projects", json={"name": "Shelved"}).json()["id"]
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.delete(f"/api/projects/{other}").status_code == 204
    receipt = client.post(f"/api/projects/{pid}/restore").json()["undo"]
    other_receipt = client.post(f"/api/projects/{other}/restore").json()["undo"]

    forged = {**receipt, "restore_event_id": other_receipt["restore_event_id"]}
    assert (
        client.post("/api/trash/projects/undo-restore", json=forged).status_code
        == 409
    )
    # The old id-list shape is a 422, not silently accepted.
    legacy = {"project_id": pid, "restored_task_ids": [], "unarchived_task_ids": []}
    assert (
        client.post("/api/trash/projects/undo-restore", json=legacy).status_code
        == 422
    )
    assert client.get(f"/api/projects/{pid}").status_code == 200


def test_undo_project_restore_leaves_independently_trashed_tasks_alone(
    client: TestClient,
) -> None:
    pid = client.post("/api/projects", json={"name": "Shelved"}).json()["id"]
    loose = client.post(f"/api/projects/{pid}/tasks", json={"title": "loose"}).json()
    client.post(f"/api/projects/{pid}/tasks", json={"title": "kept"})
    assert client.delete(f"/api/tasks/{loose['id']}").status_code == 204
    assert client.delete(f"/api/projects/{pid}").status_code == 204

    receipt = client.post(f"/api/projects/{pid}/restore").json()["undo"]
    assert len(_trashed_task_ids(client)) == 2
    assert (
        client.post("/api/trash/projects/undo-restore", json=receipt).status_code
        == 204
    )
    # Only the cascade task goes back under the project; the one trashed on its
    # own before the project delete stays in the standalone trash.
    trash = client.get("/api/trash").json()
    assert trash["projects"][0]["archived_task_count"] == 1
    assert _trashed_task_ids(client) == {loose["id"]}


def test_undo_unskip_survives_an_edit_that_it_does_not_reverse(
    client: TestClient,
) -> None:
    root = _task(client, title="Daily", due_date="2026-09-23")
    assert client.patch(f"/api/tasks/{root['id']}", json=DAILY).status_code == 200
    successor = client.post(f"/api/tasks/{root['id']}/skip").json()
    result = _restore(client, root["id"])
    # A title edit is not something moving the series back would clobber.
    renamed = client.patch(f"/api/tasks/{successor['id']}", json={"title": "Renamed"})
    assert renamed.status_code == 200

    undo = client.post("/api/trash/tasks/undo-restore", json=result["undo"])
    assert undo.status_code == 204, undo.text
    live = client.get(f"/api/tasks/{successor['id']}").json()
    assert live["title"] == "Renamed"
    assert live["due_date"] == "2026-09-24"


def test_an_old_unskip_receipt_stays_dead_after_a_newer_unskip(
    client: TestClient, db_session: Session
) -> None:
    root = _task(client, title="Daily", due_date="2026-09-23")
    assert client.patch(f"/api/tasks/{root['id']}", json=DAILY).status_code == 200
    successor = client.post(f"/api/tasks/{root['id']}/skip").json()
    first = _restore(client, root["id"])
    assert (
        client.post("/api/trash/tasks/undo-restore", json=first["undo"]).status_code
        == 204
    )
    tombstone = db_session.execute(
        select(Task.id).where(
            Task.recurrence_id == successor["recurrence_id"],
            Task.skipped_at.is_not(None),
            Task.deleted_at.is_not(None),
        )
    ).scalar_one()
    second = _restore(client, tombstone)
    assert second["task"]["id"] == successor["id"]

    stale = client.post("/api/trash/tasks/undo-restore", json=first["undo"])
    assert stale.status_code == 409
    assert client.get(f"/api/tasks/{successor['id']}").json()["due_date"] == (
        "2026-09-23"
    )
