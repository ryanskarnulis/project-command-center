"""A subtask can't depend on its own ancestor (issue #354).

A parent's completion is derived from its subtasks, so a parent waits on every
subtask; a subtask that also waits on that parent (directly or through other
tasks) is a pair that can never finish. ``add_dependency`` and a reparent both
reject the edge that would close such a wait cycle, and resolution of one that
already sits in a database is order-independent and agrees with the completion
gate.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency, TaskWorkflowStatus
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service


def _new(
    db: Session,
    title: str,
    parent: int | None = None,
    *,
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open,
) -> Task:
    task = tasks_service.create_task(
        db,
        project_id=None,
        title=title,
        parent_task_id=parent,
        workflow_status=workflow_status,
    )
    db.commit()
    return task


def _create(client: TestClient, title: str, parent: int | None = None) -> int:
    body: dict[str, Any] = {"title": title}
    if parent is not None:
        body["parent_task_id"] = parent
    resp = client.post("/api/tasks", json=body)
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


# --- add_dependency ----------------------------------------------------------


def test_subtask_cannot_depend_on_its_parent(db_session: Session) -> None:
    parent = _new(db_session, "P")
    child = _new(db_session, "C", parent.id)

    with pytest.raises(deps_service.DependencyCycleError, match="own parent"):
        deps_service.add_dependency(db_session, child.id, parent.id)
    assert deps_service.list_dependencies(db_session, child.id) == []


def test_subtask_cannot_depend_on_a_further_ancestor(db_session: Session) -> None:
    grandparent = _new(db_session, "G")
    parent = _new(db_session, "P", grandparent.id)
    child = _new(db_session, "C", parent.id)

    with pytest.raises(deps_service.DependencyCycleError):
        deps_service.add_dependency(db_session, child.id, grandparent.id)


def test_subtask_cannot_wait_on_its_ancestor_through_another_task(
    db_session: Session,
) -> None:
    """C -> X -> P with C under P: the deadlock runs through a third task."""
    parent = _new(db_session, "P")
    child = _new(db_session, "C", parent.id)
    other = _new(db_session, "X")
    deps_service.add_dependency(db_session, other.id, parent.id)
    db_session.commit()

    with pytest.raises(deps_service.DependencyCycleError, match="cycle"):
        deps_service.add_dependency(db_session, child.id, other.id)


def test_ancestor_may_depend_on_its_own_subtask(db_session: Session) -> None:
    """P -> C is redundant, not a deadlock: it clears the moment C is done."""
    parent = _new(db_session, "P")
    child = _new(db_session, "C", parent.id)
    deps_service.add_dependency(db_session, parent.id, child.id)
    db_session.commit()

    assert deps_service.is_blocked(db_session, parent.id) is True
    tasks_service.mark_done(db_session, child)
    db_session.commit()

    assert deps_service.is_blocked(db_session, parent.id) is False
    statuses = deps_service.effective_statuses(db_session, [parent.id, child.id])
    assert statuses == {
        parent.id: TaskWorkflowStatus.done,
        child.id: TaskWorkflowStatus.done,
    }


def test_issue_sequence_over_http_rejects_the_edge(client: TestClient) -> None:
    p = _create(client, "P")
    assert client.post(f"/api/tasks/{p}/done").status_code == 200
    c = _create(client, "C", p)

    resp = client.post(f"/api/tasks/{c}/dependencies", json={"depends_on_task_id": p})
    assert resp.status_code == 409, resp.text
    assert "own parent" in resp.json()["detail"]

    # Nothing was written: C is an ordinary open subtask that can be completed.
    assert client.get(f"/api/tasks/{c}/dependencies").json() == []
    assert client.get(f"/api/tasks/{c}").json()["is_blocked"] is False
    assert client.post(f"/api/tasks/{c}/done").status_code == 200


# --- reparent -----------------------------------------------------------------


def test_reparent_under_a_task_it_depends_on_rejected(db_session: Session) -> None:
    target = _new(db_session, "P")
    mover = _new(db_session, "C")
    deps_service.add_dependency(db_session, mover.id, target.id)
    db_session.commit()

    with pytest.raises(tasks_service.TaskCycleError, match="deadlock"):
        tasks_service.update_task(db_session, mover, {"parent_task_id": target.id})


def test_reparent_rejected_when_a_subtask_waits_on_the_new_ancestor(
    db_session: Session,
) -> None:
    """The moved task's subtask D waits on A; N sits under A. Moving T under N
    makes A wait on T, which waits on D, which waits on A."""
    ancestor = _new(db_session, "A")
    new_parent = _new(db_session, "N", ancestor.id)
    mover = _new(db_session, "T")
    sub = _new(db_session, "D", mover.id)
    deps_service.add_dependency(db_session, sub.id, ancestor.id)
    db_session.commit()

    with pytest.raises(tasks_service.TaskCycleError):
        tasks_service.update_task(db_session, mover, {"parent_task_id": new_parent.id})


def test_unrelated_reparent_still_allowed(db_session: Session) -> None:
    target = _new(db_session, "P")
    mover = _new(db_session, "C")
    blocker = _new(db_session, "B")
    deps_service.add_dependency(db_session, mover.id, blocker.id)
    db_session.commit()

    tasks_service.update_task(db_session, mover, {"parent_task_id": target.id})
    db_session.commit()
    assert mover.parent_task_id == target.id


def test_reparent_into_deadlock_409_over_http(client: TestClient) -> None:
    p = _create(client, "P")
    c = _create(client, "C")
    added = client.post(f"/api/tasks/{c}/dependencies", json={"depends_on_task_id": p})
    assert added.status_code == 201

    resp = client.patch(f"/api/tasks/{c}", json={"parent_task_id": p})
    assert resp.status_code == 409, resp.text
    assert client.get(f"/api/tasks/{c}").json()["parent_task_id"] is None


# --- pre-existing mixed cycles --------------------------------------------------


def _fields(rollups: dict[int, tasks_service.Rollup]) -> dict[int, tuple[Any, ...]]:
    return {
        tid: (
            r.estimated_minutes,
            r.workflow_status,
            r.has_subtasks,
            r.subtask_status,
            r.is_blocked,
        )
        for tid, r in rollups.items()
    }


def _legacy_pair(db: Session) -> tuple[Task, Task]:
    """The issue's state, as data written before the guard: P stored done, C
    under P, and a raw ``C -> P`` edge."""
    parent = _new(db, "P", workflow_status=TaskWorkflowStatus.done)
    child = _new(db, "C", parent.id)
    db.add(TaskDependency(task_id=child.id, depends_on_task_id=parent.id))
    db.commit()
    return parent, child


def test_existing_mixed_cycle_resolves_the_same_in_any_order(
    db_session: Session,
) -> None:
    parent, child = _legacy_pair(db_session)

    forward = tasks_service.compute_rollups(db_session, [parent, child])
    backward = tasks_service.compute_rollups(db_session, [child, parent])
    alone = {
        parent.id: tasks_service.compute_rollups(db_session, [parent])[parent.id],
        child.id: tasks_service.compute_rollups(db_session, [child])[child.id],
    }
    full = tasks_service.compute_rollups_for_full_set(
        db_session, tasks_service.list_tasks(db_session)
    )
    full_pair = {parent.id: full[parent.id], child.id: full[child.id]}
    assert _fields(forward) == _fields(backward) == _fields(alone) == _fields(full_pair)

    # And the shared answer is the deadlock: C blocked, P not a done leaf.
    assert forward[child.id].is_blocked is True
    assert forward[child.id].workflow_status == TaskWorkflowStatus.open
    assert forward[parent.id].has_subtasks is True
    assert forward[parent.id].workflow_status == TaskWorkflowStatus.in_progress

    # The completion gate asks the same question.
    assert deps_service.is_blocked(db_session, child.id) is True


def test_existing_mixed_cycle_read_agrees_with_done_gate(
    client: TestClient, db_session: Session
) -> None:
    p = _create(client, "P")
    assert client.post(f"/api/tasks/{p}/done").status_code == 200
    c = _create(client, "C", p)
    db_session.add(TaskDependency(task_id=c, depends_on_task_id=p))
    db_session.commit()

    read = client.get(f"/api/tasks/{c}").json()
    assert read["is_blocked"] is True
    assert client.post(f"/api/tasks/{c}/done").status_code == 409

    # Removing the bad edge is the way out, and frees both tasks.
    edges = client.get(f"/api/tasks/{c}/dependencies").json()
    assert len(edges) == 1
    removed = client.delete(f"/api/tasks/{c}/dependencies/{edges[0]['id']}")
    assert removed.status_code == 204
    assert client.post(f"/api/tasks/{c}/done").status_code == 200
    assert client.get(f"/api/tasks/{p}").json()["workflow_status"] == "done"
