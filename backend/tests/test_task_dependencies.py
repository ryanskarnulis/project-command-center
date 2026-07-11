import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import TaskWorkflowStatus
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service


def _task(
    db: Session,
    title: str,
    *,
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open,
) -> int:
    task = tasks_service.create_task(
        db,
        project_id=None,
        title=title,
        workflow_status=workflow_status,
    )
    db.commit()
    return task.id


def test_add_and_list_dependency(db_session: Session) -> None:
    a = _task(db_session, "deploy")
    b = _task(db_session, "build")

    deps_service.add_dependency(db_session, a, b)
    db_session.commit()

    deps = deps_service.list_dependencies(db_session, a)
    assert [d.depends_on_task_id for d in deps] == [b]
    # b is depended on by a.
    assert [d.task_id for d in deps_service.list_dependents(db_session, b)] == [a]


def test_self_dependency_rejected(db_session: Session) -> None:
    a = _task(db_session, "lonely")
    with pytest.raises(deps_service.SelfDependencyError):
        deps_service.add_dependency(db_session, a, a)


def test_duplicate_dependency_rejected(db_session: Session) -> None:
    a = _task(db_session, "a")
    b = _task(db_session, "b")
    deps_service.add_dependency(db_session, a, b)
    db_session.commit()
    with pytest.raises(deps_service.DuplicateDependencyError):
        deps_service.add_dependency(db_session, a, b)


def test_cycle_rejected(db_session: Session) -> None:
    a = _task(db_session, "a")
    b = _task(db_session, "b")
    c = _task(db_session, "c")
    # a -> b -> c, then c -> a would close a cycle.
    deps_service.add_dependency(db_session, a, b)
    deps_service.add_dependency(db_session, b, c)
    db_session.commit()
    with pytest.raises(deps_service.DependencyCycleError):
        deps_service.add_dependency(db_session, c, a)


def test_is_blocked_until_dependency_done(db_session: Session) -> None:
    a = _task(db_session, "publish")
    b = _task(db_session, "review")
    deps_service.add_dependency(db_session, a, b)
    db_session.commit()

    assert deps_service.is_blocked(db_session, a) is True

    b_task = tasks_service.get_task(db_session, b)
    assert b_task is not None
    tasks_service.mark_done(db_session, b_task)
    db_session.commit()

    assert deps_service.is_blocked(db_session, a) is False


def test_completing_blocked_task_rejected(db_session: Session) -> None:
    a = _task(db_session, "publish")
    b = _task(db_session, "review")
    deps_service.add_dependency(db_session, a, b)
    db_session.commit()

    a_task = tasks_service.get_task(db_session, a)
    assert a_task is not None
    with pytest.raises(tasks_service.BlockedTaskError):
        tasks_service.mark_done(db_session, a_task)

    # Once the blocker is done, completion goes through.
    b_task = tasks_service.get_task(db_session, b)
    assert b_task is not None
    tasks_service.mark_done(db_session, b_task)
    db_session.commit()
    tasks_service.mark_done(db_session, a_task)
    db_session.commit()
    assert a_task.workflow_status == TaskWorkflowStatus.done


def test_routes_completing_blocked_task_409(client: TestClient) -> None:
    a = client.post("/api/tasks", json={"title": "a"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "b"}).json()["id"]
    client.post(f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b})

    # Both the POST /done and PATCH workflow_status paths are blocked.
    assert client.post(f"/api/tasks/{a}/done").status_code == 409
    assert (
        client.patch(f"/api/tasks/{a}", json={"workflow_status": "done"}).status_code
        == 409
    )

    # Finishing the blocker unblocks completion.
    assert client.post(f"/api/tasks/{b}/done").status_code == 200
    assert client.post(f"/api/tasks/{a}/done").status_code == 200


def test_routes_add_list_remove_and_cycle_409(client: TestClient) -> None:
    a = client.post("/api/tasks", json={"title": "a"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "b"}).json()["id"]

    added = client.post(
        f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b}
    )
    assert added.status_code == 201
    body = added.json()
    assert body["depends_on_task_id"] == b
    assert body["depends_on_title"] == "b"
    assert body["depends_on_done"] is False

    listed = client.get(f"/api/tasks/{a}/dependencies")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    dependents = client.get(f"/api/tasks/{b}/dependents")
    assert dependents.status_code == 200
    dependent_body = dependents.json()
    assert len(dependent_body) == 1
    assert dependent_body[0]["dependent_task_id"] == a
    assert dependent_body[0]["dependent_title"] == "a"
    assert dependent_body[0]["dependent_done"] is False

    # b -> a would cycle.
    cycle = client.post(
        f"/api/tasks/{b}/dependencies", json={"depends_on_task_id": a}
    )
    assert cycle.status_code == 409

    edge_id = body["id"]
    removed = client.delete(f"/api/tasks/{a}/dependencies/{edge_id}")
    assert removed.status_code == 204
    assert client.get(f"/api/tasks/{a}/dependencies").json() == []


def test_remove_dependency_frees_active_edge_for_readd(client: TestClient) -> None:
    a = client.post("/api/tasks", json={"title": "a"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "b"}).json()["id"]

    first = client.post(
        f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b}
    ).json()
    client.delete(f"/api/tasks/{a}/dependencies/{first['id']}")

    # The partial unique index only covers active rows, so re-adding succeeds.
    readd = client.post(
        f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b}
    )
    assert readd.status_code == 201


def test_task_list_reports_is_blocked(client: TestClient) -> None:
    a = client.post("/api/tasks", json={"title": "a"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "b"}).json()["id"]
    client.post(f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b})

    rows = {t["id"]: t["is_blocked"] for t in client.get("/api/tasks").json()}
    assert rows[a] is True
    assert rows[b] is False

    client.post(f"/api/tasks/{b}/done")
    rows = {t["id"]: t["is_blocked"] for t in client.get("/api/tasks").json()}
    assert rows[a] is False


def test_task_list_reports_simple_top_level_blocker(client: TestClient) -> None:
    a = client.post("/api/tasks", json={"title": "downstream"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "root blocker"}).json()["id"]
    client.post(f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b})

    rows = {t["id"]: t for t in client.get("/api/tasks").json()}

    assert rows[a]["is_blocked"] is True
    assert rows[a]["is_blocking"] is False
    assert rows[a]["blocked_task_count"] == 0
    assert rows[b]["is_blocked"] is False
    assert rows[b]["is_blocking"] is True
    assert rows[b]["blocked_task_count"] == 1


def test_task_list_marks_only_highest_blocker_in_chain(
    client: TestClient,
) -> None:
    a = client.post("/api/tasks", json={"title": "leaf"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "middle"}).json()["id"]
    c = client.post("/api/tasks", json={"title": "root"}).json()["id"]
    client.post(f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b})
    client.post(f"/api/tasks/{b}/dependencies", json={"depends_on_task_id": c})

    rows = {t["id"]: t for t in client.get("/api/tasks").json()}

    assert rows[a]["is_blocked"] is True
    assert rows[a]["is_blocking"] is False
    assert rows[b]["is_blocked"] is True
    assert rows[b]["is_blocking"] is False
    assert rows[b]["blocked_task_count"] == 0
    assert rows[c]["is_blocked"] is False
    assert rows[c]["is_blocking"] is True
    assert rows[c]["blocked_task_count"] == 2


def test_top_level_blocker_count_includes_branching_downstream(
    client: TestClient,
) -> None:
    a = client.post("/api/tasks", json={"title": "branch a"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "branch b"}).json()["id"]
    c = client.post("/api/tasks", json={"title": "shared blocker"}).json()["id"]
    client.post(f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": c})
    client.post(f"/api/tasks/{b}/dependencies", json={"depends_on_task_id": c})

    rows = {t["id"]: t for t in client.get("/api/tasks").json()}

    assert rows[c]["is_blocking"] is True
    assert rows[c]["blocked_task_count"] == 2


def test_top_level_blocker_counts_ignore_done_and_deleted_tasks(
    db_session: Session,
) -> None:
    blocker = _task(db_session, "active blocker")
    active = _task(db_session, "active downstream")
    done = _task(db_session, "done downstream")
    deleted = _task(db_session, "deleted downstream")
    done_blocker = _task(
        db_session, "done blocker", workflow_status=TaskWorkflowStatus.done
    )
    downstream_of_done = _task(db_session, "downstream of done blocker")

    deps_service.add_dependency(db_session, active, blocker)
    deps_service.add_dependency(db_session, deleted, blocker)
    deps_service.add_dependency(db_session, downstream_of_done, done_blocker)

    # ``done`` must be completed before its blocking edge exists: a task can't be
    # marked done while it still waits on an open blocker. Adding the edge to an
    # already-done task is fine.
    done_task = tasks_service.get_task(db_session, done)
    deleted_task = tasks_service.get_task(db_session, deleted)
    assert done_task is not None
    assert deleted_task is not None
    tasks_service.mark_done(db_session, done_task)
    deps_service.add_dependency(db_session, done, blocker)
    tasks_service.soft_delete_task(db_session, deleted_task)
    db_session.commit()

    counts = deps_service.top_level_blocker_counts(
        db_session,
        [blocker, active, done, deleted, done_blocker, downstream_of_done],
    )

    assert counts == {blocker: 1}


def test_done_dependency_is_not_blocking(client: TestClient) -> None:
    a = client.post("/api/tasks", json={"title": "a"}).json()["id"]
    b = client.post("/api/tasks", json={"title": "b"}).json()["id"]
    client.post(f"/api/tasks/{a}/dependencies", json={"depends_on_task_id": b})

    client.post(f"/api/tasks/{b}/done")

    dep = client.get(f"/api/tasks/{a}/dependencies").json()[0]
    assert dep["depends_on_done"] is True
    assert dep["depends_on_workflow_status"] == TaskWorkflowStatus.done.value
