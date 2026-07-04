from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import Task, TaskPriority, TaskReviewStatus, TaskWorkflowStatus
from app.services import dashboard as dashboard_service
from app.services import projects as projects_service
from app.services import tasks as tasks_service


def test_task_create_markdone_softdelete(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    db_session.commit()

    task = tasks_service.create_task(
        db_session, project_id=project.id, title="audit rules"
    )
    db_session.commit()
    assert task.id is not None
    assert task.project_id == project.id
    assert task.review_status == TaskReviewStatus.accepted
    assert task.priority == TaskPriority.medium

    assert task.id in [t.id for t in tasks_service.list_tasks(db_session, project.id)]

    done = tasks_service.mark_done(db_session, task)
    db_session.commit()
    assert done.review_status == TaskReviewStatus.accepted
    assert done.workflow_status == TaskWorkflowStatus.done

    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    assert tasks_service.get_task(db_session, task.id) is None
    assert task.id not in [
        t.id for t in tasks_service.list_tasks(db_session, project.id)
    ]


def test_read_path_indexes_present_and_hot_queries_correct(
    db_session: Session,
) -> None:
    """Regression guard for the Sprint 29 task read-path indexes.

    Indexes are transparent to results, so this asserts (a) the expected index
    set is actually declared on the model — catching an accidental drop or a
    model/migration drift — and (b) the filtered read paths those indexes back
    still return exactly the right rows.
    """
    from sqlalchemy import inspect

    from app.services import task_trash as trash_service

    inspector = inspect(db_session.get_bind())
    index_names = {ix["name"] for ix in inspector.get_indexes("tasks")}
    assert {
        "ix_tasks_deleted_at_review_status",
        "ix_tasks_project_id",
        "ix_tasks_parent_task_id",
        "ix_tasks_recurrence_id",
    } <= index_names

    proj_a = projects_service.create_project(db_session, name="Alpha")
    proj_b = projects_service.create_project(db_session, name="Beta")
    db_session.commit()

    # project_id + deleted_at/review_status paths
    top = tasks_service.create_task(db_session, project_id=proj_a.id, title="top a")
    other = tasks_service.create_task(db_session, project_id=proj_b.id, title="top b")
    candidate = tasks_service.create_task(
        db_session,
        project_id=proj_a.id,
        title="unreviewed",
        review_status=TaskReviewStatus.candidate,
    )
    db_session.commit()

    # parent_task_id path (subtree fetch)
    child = tasks_service.create_task(
        db_session, project_id=proj_a.id, title="child", parent_task_id=top.id
    )
    db_session.commit()

    # deleted_at IS NOT NULL path (trash), still covered by the composite's
    # leading column.
    tasks_service.soft_delete_task(db_session, other)
    db_session.commit()

    # Active, project-scoped, accepted-only list.
    active_a = tasks_service.list_tasks(
        db_session, project_id=proj_a.id, review_status=TaskReviewStatus.accepted
    )
    active_ids = {t.id for t in active_a}
    assert top.id in active_ids and child.id in active_ids
    assert candidate.id not in active_ids  # review_status filter
    assert other.id not in active_ids  # different project AND soft-deleted

    # Subtree fetch via parent_task_id.
    subtasks = tasks_service.list_tasks(db_session, project_id=proj_a.id)
    assert child.id in {t.id for t in subtasks}
    top_level = tasks_service.list_tasks(
        db_session, project_id=proj_a.id, top_level_only=True
    )
    assert child.id not in {t.id for t in top_level}
    assert top.id in {t.id for t in top_level}

    # deleted_at IS NOT NULL scan returns the trashed row.
    trashed_ids = {t.id for t in trash_service.list_deleted_tasks(db_session)}
    assert other.id in trashed_ids
    assert top.id not in trashed_ids


def test_accepted_task_without_project_defaults_to_general(
    db_session: Session,
) -> None:
    task = tasks_service.create_task(
        db_session, project_id=None, title="unfiled visible work"
    )
    db_session.commit()

    general = projects_service.get_default_project(db_session)
    assert general is not None
    assert task.project_id == general.id


def test_candidate_without_project_stays_unfiled_until_review(
    db_session: Session,
) -> None:
    task = tasks_service.create_task(
        db_session,
        project_id=None,
        title="candidate work",
        review_status=TaskReviewStatus.candidate,
    )
    db_session.commit()

    assert task.project_id is None


def test_candidate_updated_to_accepted_defaults_to_general(
    db_session: Session,
) -> None:
    task = tasks_service.create_task(
        db_session,
        project_id=None,
        title="accepted later",
        review_status=TaskReviewStatus.candidate,
    )
    tasks_service.update_task(db_session, task, {"review_status": TaskReviewStatus.accepted})
    db_session.commit()

    general = projects_service.get_default_project(db_session)
    assert general is not None
    assert task.project_id == general.id


def test_global_tasks_route_lists_accepted_tasks_across_projects(
    client: TestClient, db_session: Session
) -> None:
    a = projects_service.create_project(db_session, name="Firewall")
    b = projects_service.create_project(db_session, name="Kitchen")
    open_a = tasks_service.create_task(db_session, project_id=a.id, title="audit rules")
    open_b = tasks_service.create_task(db_session, project_id=b.id, title="buy filters")
    tasks_service.create_task(
        db_session,
        project_id=b.id,
        title="done already",
        workflow_status=TaskWorkflowStatus.done,
    )
    db_session.commit()

    resp = client.get("/api/tasks")

    assert resp.status_code == 200
    ids = [task["id"] for task in resp.json()]
    assert ids == [open_a.id, open_b.id]


def test_deleted_project_tasks_are_trashed_with_the_project(
    client: TestClient, db_session: Session
) -> None:
    # A deleted project takes its tasks into the trash with it (cascade), rather
    # than rehoming them to General — so they drop out of the global task list.
    project = projects_service.create_project(db_session, name="Firewall")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="cascade work"
    )
    db_session.commit()

    projects_service.soft_delete_project(db_session, project)
    db_session.commit()

    resp = client.get("/api/tasks")

    assert resp.status_code == 200
    assert [row["id"] for row in resp.json()] == []
    db_session.refresh(task)
    assert task.deleted_at is not None
    assert task.deleted_with_project_id == project.id


def test_global_tasks_route_creates_task_in_general(client: TestClient) -> None:
    resp = client.post("/api/tasks", json={"title": "sort loose work"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "sort loose work"
    assert body["project_id"] is not None


def test_global_tasks_route_honors_project_id(
    client: TestClient, db_session: Session
) -> None:
    project = projects_service.create_project(db_session, name="Switches")
    db_session.commit()

    resp = client.post(
        "/api/tasks", json={"title": "rack new switch", "project_id": project.id}
    )

    assert resp.status_code == 201
    assert resp.json()["project_id"] == project.id


def test_global_tasks_route_rejects_nonexistent_project(client: TestClient) -> None:
    resp = client.post(
        "/api/tasks", json={"title": "orphan", "project_id": 999999}
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Project not found"


def test_done_task_archives_and_reopens(
    client: TestClient, db_session: Session
) -> None:
    project = projects_service.create_project(db_session, name="Firewall")
    task = tasks_service.create_task(
        db_session, project_id=project.id, title="patch box"
    )
    db_session.commit()

    # Mark done: leaves the active list, appears under the completed query.
    done = client.post(f"/api/tasks/{task.id}/done")
    assert done.status_code == 200
    assert done.json()["workflow_status"] == "done"

    active = client.get(f"/api/projects/{project.id}/tasks")
    assert [t["id"] for t in active.json()] == []

    completed = client.get(
        f"/api/projects/{project.id}/tasks", params={"workflow_status": "done"}
    )
    assert [t["id"] for t in completed.json()] == [task.id]

    # Reopen: returns to the active list, gone from the completed query.
    reopened = client.post(f"/api/tasks/{task.id}/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["workflow_status"] == "open"

    active_again = client.get(f"/api/projects/{project.id}/tasks")
    assert [t["id"] for t in active_again.json()] == [task.id]

    completed_again = client.get(
        f"/api/projects/{project.id}/tasks", params={"workflow_status": "done"}
    )
    assert completed_again.json() == []


def test_list_subtasks_returns_active_children(db_session: Session) -> None:
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    child_a = tasks_service.create_task(
        db_session, project_id=None, title="child a", parent_task_id=parent.id
    )
    child_b = tasks_service.create_task(
        db_session, project_id=None, title="child b", parent_task_id=parent.id
    )
    tasks_service.create_task(db_session, project_id=None, title="unrelated")
    db_session.commit()

    children = tasks_service.list_subtasks(db_session, parent.id)
    assert [c.id for c in children] == [child_a.id, child_b.id]


def test_cycle_guard_rejects_self_parent(db_session: Session) -> None:
    task = tasks_service.create_task(db_session, project_id=None, title="loner")
    db_session.commit()

    with pytest.raises(tasks_service.TaskCycleError):
        tasks_service.update_task(db_session, task, {"parent_task_id": task.id})


def test_cycle_guard_rejects_ancestor_cycle(db_session: Session) -> None:
    a = tasks_service.create_task(db_session, project_id=None, title="a")
    b = tasks_service.create_task(
        db_session, project_id=None, title="b", parent_task_id=a.id
    )
    db_session.commit()

    # a -> b already; making a a child of b would close a -> b -> a cycle.
    with pytest.raises(tasks_service.TaskCycleError):
        tasks_service.update_task(db_session, a, {"parent_task_id": b.id})


def test_create_subtask_route_409_on_cycle(client: TestClient) -> None:
    a = client.post("/api/tasks", json={"title": "a"}).json()
    b = client.post(
        "/api/tasks", json={"title": "b", "parent_task_id": a["id"]}
    ).json()

    resp = client.patch(f"/api/tasks/{a['id']}", json={"parent_task_id": b["id"]})
    assert resp.status_code == 409


def test_soft_delete_cascades_to_subtasks(db_session: Session) -> None:
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    child = tasks_service.create_task(
        db_session, project_id=None, title="child", parent_task_id=parent.id
    )
    grandchild = tasks_service.create_task(
        db_session, project_id=None, title="grandchild", parent_task_id=child.id
    )
    db_session.commit()

    tasks_service.soft_delete_task(db_session, parent)
    db_session.commit()

    assert tasks_service.get_task(db_session, parent.id) is None
    assert tasks_service.get_task(db_session, child.id) is None
    assert tasks_service.get_task(db_session, grandchild.id) is None


def test_estimated_minutes_round_trips_and_rejects_non_positive(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/tasks", json={"title": "write runbook", "estimated_minutes": 60}
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["estimated_minutes"] == 60

    cleared = client.patch(
        f"/api/tasks/{task_id}", json={"estimated_minutes": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["estimated_minutes"] is None

    assert (
        client.post(
            "/api/tasks", json={"title": "bad", "estimated_minutes": 0}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/tasks", json={"title": "bad", "estimated_minutes": -5}
        ).status_code
        == 422
    )


def test_list_subtasks_route_returns_direct_children(
    client: TestClient, db_session: Session
) -> None:
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    child_a = tasks_service.create_task(
        db_session, project_id=None, title="child a", parent_task_id=parent.id
    )
    child_b = tasks_service.create_task(
        db_session, project_id=None, title="child b", parent_task_id=parent.id
    )
    tasks_service.create_task(db_session, project_id=None, title="unrelated")
    db_session.commit()

    resp = client.get(f"/api/tasks/{parent.id}/subtasks")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert ids == [child_a.id, child_b.id]


def test_list_subtasks_route_404_for_missing_task(client: TestClient) -> None:
    resp = client.get("/api/tasks/999999/subtasks")
    assert resp.status_code == 404


def test_subtask_inherits_parent_project_when_none_given(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="HomeNetwork")
    db_session.commit()

    parent = tasks_service.create_task(db_session, project_id=project.id, title="parent task")
    db_session.commit()

    subtask = tasks_service.create_task(
        db_session, project_id=None, title="child task", parent_task_id=parent.id
    )
    db_session.commit()

    assert subtask.project_id == project.id


def test_subtask_keeps_explicit_project_when_given(db_session: Session) -> None:
    project_a = projects_service.create_project(db_session, name="Alpha")
    project_b = projects_service.create_project(db_session, name="Beta")
    db_session.commit()

    parent = tasks_service.create_task(db_session, project_id=project_a.id, title="parent in A")
    db_session.commit()

    subtask = tasks_service.create_task(
        db_session, project_id=project_b.id, title="child in B", parent_task_id=parent.id
    )
    db_session.commit()

    assert subtask.project_id == project_b.id


def test_task_routes_strip_and_reject_blank_text(client: TestClient) -> None:
    created = client.post(
        "/api/tasks",
        json={"title": "  Check firewall  ", "description": "   "},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["title"] == "Check firewall"
    assert created.json()["description"] is None

    updated = client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "  Check edge router  ", "description": "  spare sfp  "},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Check edge router"
    assert updated.json()["description"] == "spare sfp"

    blank_create = client.post("/api/tasks", json={"title": "   "})
    assert blank_create.status_code == 422

    blank_update = client.patch(f"/api/tasks/{task_id}", json={"title": "   "})
    assert blank_update.status_code == 422


def test_update_rejects_explicit_null_on_non_nullable_fields(client: TestClient) -> None:
    created = client.post(
        "/api/tasks",
        json={
            "title": "Audit logs",
            "description": "rotate keys",
            "due_date": "2026-07-01",
        },
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    # Explicit null on a NOT-NULL column is a 422, not a 500 / NOT-NULL violation.
    for field in ("title", "priority", "review_status", "workflow_status"):
        resp = client.patch(f"/api/tasks/{task_id}", json={field: None})
        assert resp.status_code == 422, field

    # Legitimately-nullable fields can still be cleared with an explicit null.
    cleared = client.patch(
        f"/api/tasks/{task_id}", json={"description": None, "due_date": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["description"] is None
    assert cleared.json()["due_date"] is None

    # Omitting a non-nullable field (vs. passing null) leaves it untouched: the
    # validator keys on explicit presence, not value.
    omitted = client.patch(f"/api/tasks/{task_id}", json={"description": "back"})
    assert omitted.status_code == 200
    assert omitted.json()["title"] == "Audit logs"


def test_assignee_hint_set_on_create_and_update(client: TestClient) -> None:
    created = client.post(
        "/api/tasks",
        json={"title": "Renew TLS cert", "assignee_hint": "  Dana  "},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["assignee_hint"] == "Dana"

    fetched = client.get(f"/api/tasks/{task_id}")
    assert fetched.json()["assignee_hint"] == "Dana"

    reassigned = client.patch(
        f"/api/tasks/{task_id}", json={"assignee_hint": "Sam"}
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["assignee_hint"] == "Sam"

    cleared = client.patch(f"/api/tasks/{task_id}", json={"assignee_hint": None})
    assert cleared.status_code == 200
    assert cleared.json()["assignee_hint"] is None


def test_update_task_project_id_valid(client: TestClient, db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Routers")
    db_session.commit()

    created = client.post("/api/tasks", json={"title": "Patch firmware"})
    assert created.status_code == 201
    task_id = created.json()["id"]

    moved = client.patch(f"/api/tasks/{task_id}", json={"project_id": project.id})
    assert moved.status_code == 200
    assert moved.json()["project_id"] == project.id


def test_update_task_rejects_nonexistent_project(client: TestClient) -> None:
    created = client.post("/api/tasks", json={"title": "Patch firmware"})
    assert created.status_code == 201
    task_id = created.json()["id"]

    rejected = client.patch(f"/api/tasks/{task_id}", json={"project_id": 999999})
    assert rejected.status_code == 404
    assert rejected.json()["detail"] == "Project not found"


def test_patch_accepted_task_with_null_project_rehomes_to_general(
    client: TestClient, db_session: Session
) -> None:
    # An accepted task is always filed: clearing its project with an explicit null
    # does not un-file it, it rehomes to General. The route comment and the UI both
    # depend on this contract.
    project = projects_service.create_project(db_session, name="Routers")
    db_session.commit()

    created = client.post("/api/tasks", json={"title": "Patch firmware", "project_id": project.id})
    assert created.status_code == 201
    task_id = created.json()["id"]

    cleared = client.patch(f"/api/tasks/{task_id}", json={"project_id": None})
    assert cleared.status_code == 200

    general = projects_service.get_default_project(db_session)
    assert general is not None
    assert cleared.json()["project_id"] == general.id


# --- Parent <- child roll-ups (Sprint VVV) ---------------------------------


def _accepted_subtask(db: Session, parent_id: int, **kw: Any) -> Task:
    return tasks_service.create_task(
        db, project_id=None, parent_task_id=parent_id, **kw
    )


def test_estimate_rolls_up_sum_of_children(db_session: Session) -> None:
    parent = tasks_service.create_task(
        db_session, project_id=None, title="parent", estimated_minutes=999
    )
    _accepted_subtask(db_session, parent.id, title="a", estimated_minutes=30)
    _accepted_subtask(db_session, parent.id, title="b", estimated_minutes=45)
    db_session.commit()

    rollup = tasks_service.get_rollup(db_session, parent)
    assert rollup.has_subtasks is True
    # Sum of children only — the parent's own 999 is ignored.
    assert rollup.estimated_minutes == 75


def test_estimate_rolls_up_across_two_levels(db_session: Session) -> None:
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    mid = _accepted_subtask(db_session, parent.id, title="mid", estimated_minutes=10)
    _accepted_subtask(db_session, mid.id, title="leaf1", estimated_minutes=20)
    _accepted_subtask(db_session, mid.id, title="leaf2", estimated_minutes=5)
    db_session.commit()

    # mid's own 10 is ignored (it has children); parent = 20 + 5.
    assert tasks_service.get_rollup(db_session, mid).estimated_minutes == 25
    assert tasks_service.get_rollup(db_session, parent).estimated_minutes == 25


def test_estimate_none_when_no_subtree_estimates(db_session: Session) -> None:
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    _accepted_subtask(db_session, parent.id, title="a")
    _accepted_subtask(db_session, parent.id, title="b")
    db_session.commit()

    assert tasks_service.get_rollup(db_session, parent).estimated_minutes is None


def test_leaf_rollup_keeps_own_values(db_session: Session) -> None:
    task = tasks_service.create_task(
        db_session, project_id=None, title="leaf", estimated_minutes=42
    )
    db_session.commit()
    rollup = tasks_service.get_rollup(db_session, task)
    assert rollup.has_subtasks is False
    assert rollup.estimated_minutes == 42
    assert rollup.workflow_status == TaskWorkflowStatus.open


@pytest.mark.parametrize(
    ("child_statuses", "expected"),
    [
        ([TaskWorkflowStatus.open, TaskWorkflowStatus.open], TaskWorkflowStatus.open),
        ([TaskWorkflowStatus.done, TaskWorkflowStatus.done], TaskWorkflowStatus.done),
        (
            [TaskWorkflowStatus.open, TaskWorkflowStatus.in_progress],
            TaskWorkflowStatus.in_progress,
        ),
        (
            [TaskWorkflowStatus.open, TaskWorkflowStatus.done],
            TaskWorkflowStatus.in_progress,
        ),
    ],
)
def test_status_rolls_up(
    db_session: Session,
    child_statuses: list[TaskWorkflowStatus],
    expected: TaskWorkflowStatus,
) -> None:
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    for i, status in enumerate(child_statuses):
        _accepted_subtask(
            db_session, parent.id, title=f"c{i}", workflow_status=status
        )
    db_session.commit()
    assert tasks_service.get_rollup(db_session, parent).workflow_status == expected


def test_completed_checklist_parent_leaves_open_list_and_counts(
    db_session: Session,
) -> None:
    # A checklist parent's stored workflow_status stays "open" — its status is
    # derived from its children and never written back. Once every child is done
    # the parent's EFFECTIVE status is "done", so it must drop out of the open task
    # list and the dashboard open counts, which resolve the roll-up, not the stored
    # column.
    project = projects_service.create_project(db_session, name="Chores")
    parent = tasks_service.create_task(
        db_session, project_id=project.id, title="release checklist"
    )
    child_a = tasks_service.create_task(
        db_session, project_id=project.id, parent_task_id=parent.id, title="a"
    )
    child_b = tasks_service.create_task(
        db_session, project_id=project.id, parent_task_id=parent.id, title="b"
    )
    db_session.commit()

    # One child still open -> parent effectively in-progress -> listed and counted
    # (parent + the open child; the done child drops out).
    tasks_service.mark_done(db_session, child_a)
    db_session.commit()
    open_ids = {t.id for t in tasks_service.list_tasks(db_session, exclude_done=True)}
    assert parent.id in open_ids
    total, _per_project, _recent = dashboard_service.get_overview(db_session)
    assert total == 2

    # Completing the last child rolls the parent up to done. Its stored column is
    # still "open", but its effective status is done, so it must leave both.
    tasks_service.mark_done(db_session, child_b)
    db_session.commit()

    assert parent.workflow_status == TaskWorkflowStatus.open  # stored, unchanged
    assert (
        tasks_service.get_rollup(db_session, parent).workflow_status
        == TaskWorkflowStatus.done
    )
    open_ids = {t.id for t in tasks_service.list_tasks(db_session, exclude_done=True)}
    assert parent.id not in open_ids
    total, per_project, _recent = dashboard_service.get_overview(db_session)
    assert total == 0
    assert {p.id: c for p, c in per_project}[project.id] == 0

    # It doesn't vanish: the completed view filters on effective status too, so the
    # rolled-up-done parent lands in the archive rather than nowhere.
    completed_ids = {
        t.id
        for t in tasks_service.list_tasks(
            db_session, workflow_status=TaskWorkflowStatus.done
        )
    }
    assert parent.id in completed_ids


def test_candidate_children_do_not_count(db_session: Session) -> None:
    parent = tasks_service.create_task(db_session, project_id=None, title="parent")
    tasks_service.create_task(
        db_session,
        project_id=None,
        parent_task_id=parent.id,
        title="suggested",
        review_status=TaskReviewStatus.candidate,
        estimated_minutes=99,
    )
    db_session.commit()
    rollup = tasks_service.get_rollup(db_session, parent)
    assert rollup.has_subtasks is False
    assert rollup.estimated_minutes is None


def test_subtask_inherits_priority_and_due_date(db_session: Session) -> None:
    from datetime import date

    parent = tasks_service.create_task(
        db_session,
        project_id=None,
        title="parent",
        priority=TaskPriority.high,
        due_date=date(2026, 7, 1),
    )
    child = _accepted_subtask(db_session, parent.id, title="child")
    db_session.commit()
    assert child.priority == TaskPriority.high
    assert child.due_date == date(2026, 7, 1)


def test_subtask_explicit_values_override_inheritance(db_session: Session) -> None:
    from datetime import date

    parent = tasks_service.create_task(
        db_session,
        project_id=None,
        title="parent",
        priority=TaskPriority.high,
        due_date=date(2026, 7, 1),
    )
    child = _accepted_subtask(
        db_session,
        parent.id,
        title="child",
        priority=TaskPriority.low,
        due_date=date(2026, 6, 1),
    )
    db_session.commit()
    assert child.priority == TaskPriority.low
    assert child.due_date == date(2026, 6, 1)


def test_parentless_task_defaults_to_medium(db_session: Session) -> None:
    task = tasks_service.create_task(db_session, project_id=None, title="solo")
    db_session.commit()
    assert task.priority == TaskPriority.medium


def test_changing_parent_does_not_clobber_children(db_session: Session) -> None:
    parent = tasks_service.create_task(
        db_session, project_id=None, title="parent", priority=TaskPriority.low
    )
    child = _accepted_subtask(
        db_session, parent.id, title="child", priority=TaskPriority.urgent
    )
    db_session.commit()
    tasks_service.update_task(db_session, parent, {"priority": TaskPriority.high})
    db_session.commit()
    db_session.refresh(child)
    assert child.priority == TaskPriority.urgent


def test_status_change_on_parent_rejected(client: TestClient) -> None:
    parent_id = client.post("/api/tasks", json={"title": "parent"}).json()["id"]
    client.post(
        "/api/tasks", json={"title": "child", "parent_task_id": parent_id}
    )

    patched = client.patch(
        f"/api/tasks/{parent_id}", json={"workflow_status": "done"}
    )
    assert patched.status_code == 409

    done = client.post(f"/api/tasks/{parent_id}/done")
    assert done.status_code == 409


def test_parent_read_exposes_rolled_up_values(client: TestClient) -> None:
    parent_id = client.post("/api/tasks", json={"title": "parent"}).json()["id"]
    client.post(
        "/api/tasks",
        json={
            "title": "child",
            "parent_task_id": parent_id,
            "estimated_minutes": 60,
            "workflow_status": "in_progress",
        },
    )
    body = client.get(f"/api/tasks/{parent_id}").json()
    assert body["has_subtasks"] is True
    assert body["estimated_minutes"] == 60
    assert body["workflow_status"] == "in_progress"
