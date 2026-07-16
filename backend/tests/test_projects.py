import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent
from app.services import projects as projects_service
from app.services import task_trash as task_trash_service
from app.services import tasks as tasks_service


def test_project_create_get_list_softdelete(db_session: Session) -> None:
    created = projects_service.create_project(
        db_session, name="Firewall", description="net cleanup"
    )
    db_session.commit()
    assert created.id is not None
    assert created.name == "Firewall"
    assert created.description == "net cleanup"
    assert created.created_at is not None
    assert created.deleted_at is None

    fetched = projects_service.get_project(db_session, created.id)
    assert fetched is not None
    assert fetched.id == created.id

    assert created.id in [p.id for p in projects_service.list_projects(db_session)]

    projects_service.soft_delete_project(db_session, created)
    db_session.commit()

    assert projects_service.get_project(db_session, created.id) is None
    assert created.id not in [p.id for p in projects_service.list_projects(db_session)]


def test_ensure_default_project_is_idempotent(db_session: Session) -> None:
    first = projects_service.ensure_default_project(db_session)
    second = projects_service.ensure_default_project(db_session)
    db_session.commit()

    assert first.id == second.id
    assert first.name == "General"
    assert first.system_key == "general"
    assert first.is_protected is True


def test_soft_delete_project_cascades_tasks(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    parent = tasks_service.create_task(
        db_session, project_id=project.id, title="audit rules"
    )
    child = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="child rule",
        parent_task_id=parent.id,
    )
    already_trashed = tasks_service.create_task(
        db_session, project_id=project.id, title="old rules"
    )
    tasks_service.soft_delete_task(db_session, already_trashed)
    db_session.commit()

    projects_service.soft_delete_project(db_session, project)
    db_session.commit()

    db_session.refresh(parent)
    db_session.refresh(child)
    db_session.refresh(already_trashed)
    # Active tasks + subtree go to trash WITH the project, stamped + still owned.
    assert parent.deleted_at is not None
    assert child.deleted_at is not None
    assert parent.deleted_with_project_id == project.id
    assert child.deleted_with_project_id == project.id
    assert parent.project_id == project.id
    # A task trashed independently beforehand keeps its null marker (not swept up).
    assert already_trashed.deleted_with_project_id is None
    assert projects_service.get_project(db_session, project.id) is None


def test_restore_project_with_and_without_tasks(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    parent = tasks_service.create_task(
        db_session, project_id=project.id, title="audit rules"
    )
    child = tasks_service.create_task(
        db_session, project_id=project.id, title="child", parent_task_id=parent.id
    )
    db_session.commit()
    projects_service.soft_delete_project(db_session, project)
    db_session.commit()

    # Decline: only the project shell returns; tasks stay in trash.
    restored, count = projects_service.restore_project(
        db_session, project, restore_tasks=False
    )
    db_session.commit()
    assert count == 0
    db_session.refresh(parent)
    assert parent.deleted_at is not None

    # Re-delete, then restore WITH tasks: the whole subtree comes back, marker cleared.
    projects_service.soft_delete_project(db_session, project)
    db_session.commit()
    restored, count = projects_service.restore_project(
        db_session, project, restore_tasks=True
    )
    db_session.commit()
    assert count == 2
    db_session.refresh(parent)
    db_session.refresh(child)
    assert parent.deleted_at is None
    assert child.deleted_at is None
    assert parent.deleted_with_project_id is None
    assert child.deleted_with_project_id is None


def test_restore_project_skips_independently_trashed_tasks(
    db_session: Session,
) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    cascade = tasks_service.create_task(
        db_session, project_id=project.id, title="active task"
    )
    independent = tasks_service.create_task(
        db_session, project_id=project.id, title="pre-trashed"
    )
    tasks_service.soft_delete_task(db_session, independent)
    db_session.commit()

    projects_service.soft_delete_project(db_session, project)
    db_session.commit()
    _, count = projects_service.restore_project(
        db_session, project, restore_tasks=True
    )
    db_session.commit()

    assert count == 1
    db_session.refresh(cascade)
    db_session.refresh(independent)
    assert cascade.deleted_at is None
    # The independently-trashed task was NOT swept back.
    assert independent.deleted_at is not None


def test_cross_project_subtree_round_trips_through_the_safety_sweep(
    db_session: Session,
) -> None:
    # A child may sit in a different project than its parent (create_task only
    # inherits the parent's project when none is given). Such a child is reached
    # by the safety sweep, not the top-level pass — and its own descendants must
    # still be stamped, or they stay trashed when the project is restored.
    project_a = projects_service.create_project(db_session, name="Alpha")
    project_b = projects_service.create_project(db_session, name="Bravo")
    parent = tasks_service.create_task(
        db_session, project_id=project_a.id, title="lives in A"
    )
    swept = tasks_service.create_task(
        db_session,
        project_id=project_b.id,
        title="child in B",
        parent_task_id=parent.id,
    )
    grandchild = tasks_service.create_task(
        db_session,
        project_id=project_a.id,
        title="grandchild back in A",
        parent_task_id=swept.id,
    )
    db_session.commit()

    projects_service.soft_delete_project(db_session, project_b)
    db_session.commit()

    db_session.refresh(parent)
    db_session.refresh(swept)
    db_session.refresh(grandchild)
    # The swept task AND its cross-project descendant go down stamped together.
    assert swept.deleted_at is not None
    assert grandchild.deleted_at is not None
    assert swept.deleted_with_project_id == project_b.id
    assert grandchild.deleted_with_project_id == project_b.id
    # The parent lives in another project and is untouched.
    assert parent.deleted_at is None

    _, count = projects_service.restore_project(
        db_session, project_b, restore_tasks=True
    )
    db_session.commit()

    assert count == 2
    db_session.refresh(swept)
    db_session.refresh(grandchild)
    assert swept.deleted_at is None
    assert grandchild.deleted_at is None
    assert swept.deleted_with_project_id is None
    assert grandchild.deleted_with_project_id is None


def test_swept_subtree_is_hidden_from_the_standalone_task_trash(
    db_session: Session,
) -> None:
    # A stamped row belongs to the project's trash entry; surfacing it loose in
    # the Tasks trash is what made the missing subtree user-visible.
    project_a = projects_service.create_project(db_session, name="Alpha")
    project_b = projects_service.create_project(db_session, name="Bravo")
    parent = tasks_service.create_task(
        db_session, project_id=project_a.id, title="lives in A"
    )
    swept = tasks_service.create_task(
        db_session,
        project_id=project_b.id,
        title="child in B",
        parent_task_id=parent.id,
    )
    grandchild = tasks_service.create_task(
        db_session,
        project_id=project_a.id,
        title="grandchild back in A",
        parent_task_id=swept.id,
    )
    db_session.commit()

    projects_service.soft_delete_project(db_session, project_b)
    db_session.commit()

    trashed_ids = [t.id for t in task_trash_service.list_deleted_tasks(db_session)]
    assert swept.id not in trashed_ids
    assert grandchild.id not in trashed_ids
    assert (
        projects_service.count_tasks_deleted_with_project(db_session, project_b.id) == 2
    )


def test_safety_sweep_deletes_each_task_once(db_session: Session) -> None:
    # The sweep reads its rows up front, so a task an earlier iteration's cascade
    # already deleted must be skipped: soft_delete re-stamps deleted_at
    # unconditionally and the event log would fire a second time.
    project_a = projects_service.create_project(db_session, name="Alpha")
    project_b = projects_service.create_project(db_session, name="Bravo")
    parent = tasks_service.create_task(
        db_session, project_id=project_a.id, title="lives in A"
    )
    swept = tasks_service.create_task(
        db_session,
        project_id=project_b.id,
        title="child in B",
        parent_task_id=parent.id,
    )
    # Also in B, and under `swept` — so the sweep query returns both of them.
    descendant = tasks_service.create_task(
        db_session,
        project_id=project_b.id,
        title="descendant also in B",
        parent_task_id=swept.id,
    )
    db_session.commit()

    projects_service.soft_delete_project(db_session, project_b)
    db_session.commit()

    db_session.refresh(descendant)
    assert descendant.deleted_with_project_id == project_b.id
    deleted_at_after_sweep = descendant.deleted_at

    for task_id in (swept.id, descendant.id):
        events = (
            db_session.execute(
                select(ActivityEvent).where(
                    ActivityEvent.entity_type == "task",
                    ActivityEvent.entity_id == task_id,
                    ActivityEvent.action == "deleted",
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1, f"task {task_id} logged {len(events)} deleted events"

    # And its trash timestamp was not moved by a second delete.
    db_session.refresh(descendant)
    assert descendant.deleted_at == deleted_at_after_sweep


def test_soft_delete_project_refuses_general(db_session: Session) -> None:
    general = projects_service.ensure_default_project(db_session)
    db_session.commit()

    with pytest.raises(ValueError):
        projects_service.soft_delete_project(db_session, general)

    assert projects_service.get_project(db_session, general.id) is not None


def test_project_routes_strip_and_reject_blank_text(client: TestClient) -> None:
    created = client.post(
        "/api/projects",
        json={"name": "  Firewall  ", "description": "   "},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert created.json()["name"] == "Firewall"
    assert created.json()["description"] is None

    updated = client.patch(
        f"/api/projects/{project_id}",
        json={"name": "  Edge Router  ", "description": "  hardware refresh  "},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Edge Router"
    assert updated.json()["description"] == "hardware refresh"

    blank_create = client.post("/api/projects", json={"name": "   "})
    assert blank_create.status_code == 422

    blank_update = client.patch(f"/api/projects/{project_id}", json={"name": "   "})
    assert blank_update.status_code == 422


def test_delete_general_route_returns_409(
    client: TestClient, db_session: Session
) -> None:
    general = projects_service.ensure_default_project(db_session)
    db_session.commit()

    resp = client.delete(f"/api/projects/{general.id}")

    assert resp.status_code == 409
    assert projects_service.get_project(db_session, general.id) is not None


def test_reorder_projects_route(client: TestClient) -> None:
    ids = [
        client.post("/api/projects", json={"name": name}).json()["id"]
        for name in ("Alpha", "Beta", "Gamma")
    ]
    listed = client.get("/api/projects").json()
    order = [p["id"] for p in listed]

    reversed_order = list(reversed(order))
    res = client.put("/api/projects/order", json={"project_ids": reversed_order})
    assert res.status_code == 200
    assert [p["id"] for p in res.json()] == reversed_order
    assert [p["id"] for p in client.get("/api/projects").json()] == reversed_order
    assert ids[0] in reversed_order

    # Stale/partial id sets are rejected so a lagging client can't drop rows.
    res = client.put("/api/projects/order", json={"project_ids": order[:-1]})
    assert res.status_code == 409


def test_new_project_appends_after_reorder(db_session: Session) -> None:
    a = projects_service.create_project(db_session, name="A")
    b = projects_service.create_project(db_session, name="B")
    db_session.commit()
    projects_service.reorder_projects(db_session, [b.id, a.id])
    db_session.commit()

    c = projects_service.create_project(db_session, name="C")
    db_session.commit()
    assert [p.id for p in projects_service.list_projects(db_session)] == [
        b.id,
        a.id,
        c.id,
    ]


def test_close_and_reopen_project(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Old initiative")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="lingering task"
    )
    db_session.commit()

    projects_service.close_project(db_session, project)
    db_session.commit()
    assert project.closed_at is not None
    # Hidden from the default list, still fetchable directly, tasks untouched.
    assert project.id not in [p.id for p in projects_service.list_projects(db_session)]
    assert project.id in [
        p.id for p in projects_service.list_projects(db_session, include_closed=True)
    ]
    assert projects_service.get_project(db_session, project.id) is not None
    fetched_task = tasks_service.get_task(db_session, task.id)
    assert fetched_task is not None and fetched_task.deleted_at is None

    projects_service.reopen_project(db_session, project)
    db_session.commit()
    assert project.closed_at is None
    assert project.id in [p.id for p in projects_service.list_projects(db_session)]


def test_close_project_refuses_general(db_session: Session) -> None:
    general = projects_service.ensure_default_project(db_session)
    db_session.commit()
    with pytest.raises(ValueError, match="protected"):
        projects_service.close_project(db_session, general)


def test_close_and_reopen_routes(client: TestClient) -> None:
    created = client.post("/api/projects", json={"name": "Route close"}).json()
    project_id = created["id"]

    closed = client.post(f"/api/projects/{project_id}/close")
    assert closed.status_code == 200
    assert closed.json()["closed_at"] is not None

    default_list = client.get("/api/projects").json()
    assert project_id not in [p["id"] for p in default_list]
    full_list = client.get("/api/projects?include_closed=true").json()
    assert project_id in [p["id"] for p in full_list]

    reopened = client.post(f"/api/projects/{project_id}/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["closed_at"] is None
