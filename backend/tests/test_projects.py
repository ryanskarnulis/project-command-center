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


def test_ensure_default_project_adopts_oldest_of_duplicate_names(
    db_session: Session,
) -> None:
    # Project names aren't unique, so a database that never ran the seed migration
    # can hold several ordinary projects called "General". Adoption takes the oldest
    # instead of raising MultipleResultsFound.
    older = projects_service.create_project(db_session, name="General")
    newer = projects_service.create_project(db_session, name="General")
    db_session.commit()
    assert older.system_key is None
    assert newer.system_key is None

    adopted = projects_service.ensure_default_project(db_session)
    db_session.commit()

    assert adopted.id == older.id
    assert adopted.system_key == "general"
    assert adopted.is_protected is True
    db_session.refresh(newer)
    assert newer.system_key is None


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


def test_restore_project_without_tasks_surfaces_them_as_standalone_trash(
    db_session: Session,
) -> None:
    # Declining "bring back tasks" must not strand them: the cascade marker is
    # cleared so they move into the standalone Tasks trash, still individually
    # restorable into the now-active project.
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

    _, count = projects_service.restore_project(
        db_session, project, restore_tasks=False
    )
    db_session.commit()

    assert count == 0
    db_session.refresh(parent)
    db_session.refresh(child)
    # Still trashed, but now unstamped -> visible in the standalone Tasks trash.
    assert parent.deleted_at is not None
    assert child.deleted_at is not None
    assert parent.deleted_with_project_id is None
    assert child.deleted_with_project_id is None
    standalone_ids = [t.id for t in task_trash_service.list_deleted_tasks(db_session)]
    assert parent.id in standalone_ids
    assert child.id in standalone_ids

    # And each restores individually back into the now-active project.
    restored_parent = task_trash_service.restore_task(db_session, parent)
    db_session.commit()
    assert restored_parent.deleted_at is None
    assert restored_parent.project_id == project.id


def test_restore_project_with_tasks_brings_back_the_subtree(
    db_session: Session,
) -> None:
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

    _, count = projects_service.restore_project(
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


def test_restore_project_with_tasks_logs_a_restored_event_per_task(
    db_session: Session,
) -> None:
    # Restore is itemized like delete: each cascade task gets its own "restored"
    # event, so the audit trail isn't just a single project-level row.
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

    projects_service.restore_project(db_session, project, restore_tasks=True)
    db_session.commit()

    for task_id in (parent.id, child.id):
        events = (
            db_session.execute(
                select(ActivityEvent).where(
                    ActivityEvent.entity_type == "task",
                    ActivityEvent.entity_id == task_id,
                    ActivityEvent.action == "restored",
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1, f"task {task_id} logged {len(events)} restored events"


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


def test_trashing_a_project_leaves_a_childs_foreign_descendant_active(
    db_session: Session,
) -> None:
    # Cascade follows project membership, not hierarchy. A child may sit in a
    # different project than its parent (create_task only inherits the parent's
    # project when none is given). Trashing project B takes only B's own tasks; a
    # descendant filed under active project A stays active even though its parent
    # chain runs through B.
    project_a = projects_service.create_project(db_session, name="Alpha")
    project_b = projects_service.create_project(db_session, name="Bravo")
    parent = tasks_service.create_task(
        db_session, project_id=project_a.id, title="lives in A"
    )
    child_in_b = tasks_service.create_task(
        db_session,
        project_id=project_b.id,
        title="child in B",
        parent_task_id=parent.id,
    )
    grandchild_in_a = tasks_service.create_task(
        db_session,
        project_id=project_a.id,
        title="grandchild back in A",
        parent_task_id=child_in_b.id,
    )
    db_session.commit()

    projects_service.soft_delete_project(db_session, project_b)
    db_session.commit()

    db_session.refresh(parent)
    db_session.refresh(child_in_b)
    db_session.refresh(grandchild_in_a)
    # Only B's own task is trashed and stamped.
    assert child_in_b.deleted_at is not None
    assert child_in_b.deleted_with_project_id == project_b.id
    # The parent (A) and the grandchild (A) stay active — membership wins.
    assert parent.deleted_at is None
    assert grandchild_in_a.deleted_at is None
    assert grandchild_in_a.deleted_with_project_id is None

    _, count = projects_service.restore_project(
        db_session, project_b, restore_tasks=True
    )
    db_session.commit()

    assert count == 1
    db_session.refresh(child_in_b)
    assert child_in_b.deleted_at is None
    assert child_in_b.deleted_with_project_id is None


def test_membership_trashed_task_is_hidden_from_the_standalone_task_trash(
    db_session: Session,
) -> None:
    # A stamped row belongs to the project's trash entry, not the loose Tasks
    # trash. Its foreign-project descendant, left active, isn't in trash at all.
    project_a = projects_service.create_project(db_session, name="Alpha")
    project_b = projects_service.create_project(db_session, name="Bravo")
    parent = tasks_service.create_task(
        db_session, project_id=project_a.id, title="lives in A"
    )
    child_in_b = tasks_service.create_task(
        db_session,
        project_id=project_b.id,
        title="child in B",
        parent_task_id=parent.id,
    )
    grandchild_in_a = tasks_service.create_task(
        db_session,
        project_id=project_a.id,
        title="grandchild back in A",
        parent_task_id=child_in_b.id,
    )
    db_session.commit()

    projects_service.soft_delete_project(db_session, project_b)
    db_session.commit()

    trashed_ids = [t.id for t in task_trash_service.list_deleted_tasks(db_session)]
    assert child_in_b.id not in trashed_ids
    db_session.refresh(grandchild_in_a)
    assert grandchild_in_a.deleted_at is None  # still active, never in trash
    assert (
        projects_service.count_tasks_deleted_with_project(db_session, project_b.id) == 1
    )


def test_membership_cascade_deletes_each_task_once(db_session: Session) -> None:
    # The membership query returns each of the project's tasks exactly once, so a
    # parent and its same-project child are each soft-deleted (and logged) once.
    project = projects_service.create_project(db_session, name="Bravo")
    parent = tasks_service.create_task(
        db_session, project_id=project.id, title="parent in B"
    )
    child = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="child in B",
        parent_task_id=parent.id,
    )
    db_session.commit()

    projects_service.soft_delete_project(db_session, project)
    db_session.commit()

    db_session.refresh(child)
    assert child.deleted_with_project_id == project.id

    for task_id in (parent.id, child.id):
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


def test_project_update_rejects_explicit_null_name(client: TestClient) -> None:
    created = client.post(
        "/api/projects",
        json={"name": "Firewall", "description": "perimeter"},
    )
    project_id = created.json()["id"]

    # ``name`` is NOT NULL: an explicit null is a 422, not a NOT-NULL violation.
    null_name = client.patch(f"/api/projects/{project_id}", json={"name": None})
    assert null_name.status_code == 422

    # ``description`` is nullable, so an explicit null still clears it.
    null_description = client.patch(
        f"/api/projects/{project_id}", json={"description": None}
    )
    assert null_description.status_code == 200
    assert null_description.json()["description"] is None
    assert null_description.json()["name"] == "Firewall"

    # An omitted ``name`` is untouched, not treated as a null.
    omitted_name = client.patch(
        f"/api/projects/{project_id}", json={"description": "rebuilt"}
    )
    assert omitted_name.status_code == 200
    assert omitted_name.json()["name"] == "Firewall"
    assert omitted_name.json()["description"] == "rebuilt"


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
