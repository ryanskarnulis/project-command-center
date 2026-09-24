"""The *stored* task hierarchy stays acyclic, trashed rows included (#305).

``_assert_no_parent_cycle`` used to stop at the first trashed ancestor. That
describes the active forest correctly — an orphan of a trashed parent is an
effective root (#128) — but a trashed row keeps its ``parent_task_id``, and
restore reconnects it. So a reparent could commit A -> C -> B -> A through a
trashed B, and restoring B then recursed forever in the roll-up (HTTP 500).

The guard now walks the raw stored pointers, so the reparent is a 409. Restore
is defensive against a cycle already sitting in someone's database: the restored
row that closes it is detached to top level (audited), never a 500.
"""

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, Task
from app.services import projects as projects_service
from app.services import task_trash
from app.services import tasks as tasks_service


def _create(client: TestClient, title: str, **extra: object) -> int:
    response = client.post("/api/tasks", json={"title": title, **extra})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _chain(client: TestClient, **extra: object) -> tuple[int, int, int]:
    """A, B under A, C under B."""
    a = _create(client, "A")
    b = _create(client, "B", parent_task_id=a, **extra)
    c = _create(client, "C", parent_task_id=b, **extra)
    return a, b, c


def test_reparent_through_a_trashed_ancestor_is_rejected(client: TestClient) -> None:
    """The issue's repro: delete B, restore C alone, move A under C, restore B."""
    a, b, c = _chain(client)
    assert client.delete(f"/api/tasks/{b}").status_code == 204
    assert client.post(f"/api/tasks/{c}/restore").status_code == 200

    # C is an effective orphan still pointing at trashed B; A -> C would close
    # A -> C -> B -> A in the stored pointers.
    moved = client.patch(f"/api/tasks/{a}", json={"parent_task_id": c})
    assert moved.status_code == 409, moved.text
    assert client.get(f"/api/tasks/{a}").json()["parent_task_id"] is None

    # And the ancestor restore that used to 500 now just works.
    restored = client.post(f"/api/tasks/{b}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["parent_task_id"] == a
    subtasks = client.get(f"/api/tasks/{b}/subtasks")
    assert [t["id"] for t in subtasks.json()] == [c]


def test_create_under_orphan_still_allowed_but_cycle_reparent_is_not(
    client: TestClient,
) -> None:
    """Happy path (#128 stays green): an orphan still takes new and moved children."""
    a, b, c = _chain(client)
    assert client.delete(f"/api/tasks/{b}").status_code == 204
    assert client.post(f"/api/tasks/{c}/restore").status_code == 200

    sub = _create(client, "sub of orphan", parent_task_id=c)
    mover = _create(client, "mover")
    moved = client.patch(f"/api/tasks/{mover}", json={"parent_task_id": c})
    assert moved.status_code == 200, moved.text
    assert moved.json()["parent_task_id"] == c
    assert client.get(f"/api/tasks/{sub}").json()["parent_task_id"] == c

    # A itself is an unrelated root in the active forest, but its stored
    # descendant B sits above C — so it is the one task C can't adopt.
    assert (
        client.patch(f"/api/tasks/{a}", json={"parent_task_id": c}).status_code
        == 409
    )


def test_project_cascade_variant_is_rejected_then_restores_cleanly(
    client: TestClient,
) -> None:
    """Same invariant when the trashing and restoring go through a project."""
    project = client.post("/api/projects", json={"name": "Doomed"}).json()["id"]
    a = _create(client, "A")  # General
    b = _create(client, "B", parent_task_id=a, project_id=project)
    c = _create(client, "C", parent_task_id=b, project_id=project)

    assert client.delete(f"/api/projects/{project}").status_code == 204
    # C alone comes back (rehomed to General), still pointing at trashed B.
    assert client.post(f"/api/tasks/{c}/restore").status_code == 200

    moved = client.patch(f"/api/tasks/{a}", json={"parent_task_id": c})
    assert moved.status_code == 409, moved.text

    restored = client.post(f"/api/projects/{project}/restore?restore_tasks=true")
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/tasks/{b}").json()["parent_task_id"] == a


# --- Restore is defensive against a cycle already in the database ------------


def _force_cycle(db: Session, a: int, c: int) -> None:
    """Write A -> C behind the service's back: the pre-fix corrupt state."""
    db.execute(update(Task).where(Task.id == a).values(parent_task_id=c))
    db.commit()


def _detach_events(db: Session, task_id: int) -> list[ActivityEvent]:
    return [
        e
        for e in db.query(ActivityEvent)
        .filter(ActivityEvent.entity_type == "task", ActivityEvent.entity_id == task_id)
        .all()
        if "cycle" in e.summary
    ]


def test_restore_breaks_a_preexisting_cycle_by_detaching_the_restored_row(
    client: TestClient, db_session: Session
) -> None:
    a, b, c = _chain(client)
    assert client.delete(f"/api/tasks/{b}").status_code == 204
    assert client.post(f"/api/tasks/{c}/restore").status_code == 200
    _force_cycle(db_session, a, c)

    restored = client.post(f"/api/tasks/{b}/restore")
    assert restored.status_code == 200, restored.text
    # B closed the loop, so B is the one detached; A -> C -> B survives.
    assert restored.json()["parent_task_id"] is None
    assert client.get(f"/api/tasks/{a}").json()["parent_task_id"] == c
    assert client.get(f"/api/tasks/{c}").json()["parent_task_id"] == b
    db_session.expire_all()
    assert len(_detach_events(db_session, b)) == 1


def test_project_restore_breaks_a_preexisting_cycle(
    client: TestClient, db_session: Session
) -> None:
    project = client.post("/api/projects", json={"name": "Doomed"}).json()["id"]
    a = _create(client, "A")
    b = _create(client, "B", parent_task_id=a, project_id=project)
    c = _create(client, "C", parent_task_id=b, project_id=project)
    assert client.delete(f"/api/projects/{project}").status_code == 204
    assert client.post(f"/api/tasks/{c}/restore").status_code == 200
    _force_cycle(db_session, a, c)

    restored = client.post(f"/api/projects/{project}/restore?restore_tasks=true")
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/tasks/{b}").json()["parent_task_id"] is None
    db_session.expire_all()
    assert len(_detach_events(db_session, b)) == 1


def test_project_restore_detaches_one_row_of_a_cycle_restored_together(
    db_session: Session,
) -> None:
    """Both halves of a two-cycle come back in one batch; exactly one detaches."""
    project = projects_service.create_project(db_session, name="Loop")
    x = tasks_service.create_task(db_session, project_id=project.id, title="X")
    y = tasks_service.create_task(
        db_session, project_id=project.id, title="Y", parent_task_id=x.id
    )
    db_session.commit()
    projects_service.soft_delete_project(db_session, project)
    db_session.commit()
    _force_cycle(db_session, x.id, y.id)  # X <-> Y, both trashed

    deleted = projects_service.get_deleted_project(db_session, project.id)
    assert deleted is not None
    _, count = projects_service.restore_project(
        db_session, deleted, restore_tasks=True
    )
    db_session.commit()
    assert count == 2
    db_session.refresh(x)
    db_session.refresh(y)
    parents = {x.parent_task_id, y.parent_task_id}
    assert None in parents and parents != {None}
    # Roll-ups resolve without recursing forever.
    tasks_service.compute_rollups(db_session, [x, y])


def test_rollup_terminates_on_an_active_cycle(db_session: Session) -> None:
    """Belt and braces: no roll-up path recurses forever on corrupt data."""
    x = tasks_service.create_task(db_session, project_id=None, title="X")
    y = tasks_service.create_task(
        db_session, project_id=None, title="Y", parent_task_id=x.id
    )
    db_session.commit()
    _force_cycle(db_session, x.id, y.id)
    db_session.expire_all()
    rollups = tasks_service.compute_rollups(db_session, [x, y])
    assert set(rollups) == {x.id, y.id}


def test_task_subtree_restore_breaks_a_preexisting_cycle(
    client: TestClient, db_session: Session
) -> None:
    a, b, c = _chain(client)
    assert client.delete(f"/api/tasks/{b}").status_code == 204
    assert client.post(f"/api/tasks/{c}/restore").status_code == 200
    _force_cycle(db_session, a, c)

    trashed = task_trash.get_deleted_task(db_session, b)
    assert trashed is not None
    root, _ = task_trash.restore_task_subtree(db_session, trashed)
    db_session.commit()
    assert root.parent_task_id is None
