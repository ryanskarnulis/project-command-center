"""BUG #254: a purge that severs a dependency edge must audit the survivor.

``purge_task`` hard-deletes every dependency row touching the purge set in one
bulk statement. When the *other* endpoint survives, that task's dependency list
visibly changes — and it used to change with no ``activity_events`` row at all,
the same class of silent structural mutation as the parent detach (#242) and the
removal of an edge whose blocker sits in trash (#201), both already audited.

These tests pin the event on every purge caller: the single-task purge route, the
bulk ``/trash/purge``, empty trash, and a project purge (which reaches
``purge_task`` through ``projects.purge_project``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, Task, TaskDependency
from app.services import activity, task_trash


def _events_for(db: Session, task_id: int) -> list[ActivityEvent]:
    """Every recorded event row for one task, oldest first."""
    db.expire_all()
    return list(
        db.execute(
            select(ActivityEvent)
            .where(
                ActivityEvent.entity_type == "task",
                ActivityEvent.entity_id == task_id,
            )
            .order_by(ActivityEvent.id)
        ).scalars()
    )


def _actions_for(db: Session, task_id: int) -> list[str]:
    return [e.action for e in _events_for(db, task_id)]


def _dependency_removed_events(db: Session) -> list[ActivityEvent]:
    """Every ``dependency_removed`` row in the log, whoever it belongs to."""
    db.expire_all()
    return list(
        db.execute(
            select(ActivityEvent)
            .where(ActivityEvent.action == "dependency_removed")
            .order_by(ActivityEvent.id)
        ).scalars()
    )


def _edge_count(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(TaskDependency)) or 0


def _add_edge(client: TestClient, dependent_id: int, blocker_id: int) -> int:
    resp = client.post(
        f"/api/tasks/{dependent_id}/dependencies",
        json={"depends_on_task_id": blocker_id},
    )
    assert resp.status_code == 201
    edge_id: int = resp.json()["id"]
    return edge_id


def test_purging_a_blocker_audits_the_surviving_dependent(
    client: TestClient, db_session: Session
) -> None:
    """The reproduction from the issue: A waits on B, B is trashed, then purged.

    A lives on with one fewer blocker and B's title exists nowhere else
    afterwards, so the event has to carry the snapshot.
    """
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    survivor = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "A survivor"}
    ).json()["id"]
    blocker = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "B doomed blocker"}
    ).json()["id"]
    _add_edge(client, survivor, blocker)

    assert client.delete(f"/api/tasks/{blocker}").status_code == 204
    assert client.delete(f"/api/tasks/{blocker}/purge").status_code == 204

    db_session.expire_all()
    assert db_session.get(Task, blocker) is None
    assert _edge_count(db_session) == 0  # the bulk delete still takes the row

    events = _events_for(db_session, survivor)
    assert [e.action for e in events] == [
        "created",
        "dependency_added",
        "dependency_removed",
    ]
    severed = events[-1]
    assert severed.project_id == pid
    assert (
        severed.summary
        == 'Task "A survivor" no longer waits on permanently deleted "B doomed blocker"'
    )


def test_purging_a_dependent_audits_the_surviving_blocker(
    client: TestClient, db_session: Session
) -> None:
    """The other direction: the purged row was the one doing the waiting.

    B keeps existing and quietly stops having anything queued behind it. There is
    no manual counterpart for this side (``remove_dependency`` only writes on the
    dependent), but the survivor is still the only row left to record it against.
    """
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    dependent = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "A doomed dependent"}
    ).json()["id"]
    survivor = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "B survivor"}
    ).json()["id"]
    _add_edge(client, dependent, survivor)

    assert client.delete(f"/api/tasks/{dependent}").status_code == 204
    assert client.delete(f"/api/tasks/{dependent}/purge").status_code == 204

    db_session.expire_all()
    assert db_session.get(Task, dependent) is None
    assert _edge_count(db_session) == 0

    events = _events_for(db_session, survivor)
    assert [e.action for e in events] == ["created", "dependency_removed"]
    severed = events[-1]
    assert severed.project_id == pid
    assert (
        severed.summary
        == 'Task "B survivor" no longer blocks permanently deleted "A doomed dependent"'
    )


def test_purge_audits_every_edge_a_survivor_loses(
    client: TestClient, db_session: Session
) -> None:
    """One event per severed edge, matching ``remove_dependency``'s per-edge event.

    The purged parent takes its trashed child with it, and the survivor was
    waiting on both.
    """
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    parent = client.post(f"/api/projects/{pid}/tasks", json={"title": "P"}).json()["id"]
    child = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "C", "parent_task_id": parent}
    ).json()["id"]
    survivor = client.post(f"/api/projects/{pid}/tasks", json={"title": "S"}).json()[
        "id"
    ]
    _add_edge(client, survivor, parent)
    _add_edge(client, survivor, child)

    assert client.delete(f"/api/tasks/{parent}").status_code == 204  # cascades to C
    assert client.delete(f"/api/tasks/{parent}/purge").status_code == 204

    summaries = [
        e.summary
        for e in _events_for(db_session, survivor)
        if e.action == "dependency_removed"
    ]
    # Ordered by edge id, so the feed replays them in the order they were added.
    assert summaries == [
        'Task "S" no longer waits on permanently deleted "P"',
        'Task "S" no longer waits on permanently deleted "C"',
    ]


def test_bulk_purge_audits_the_surviving_dependent(
    client: TestClient, db_session: Session
) -> None:
    """The ``/trash/purge`` caller inherits it — the fix lives inside ``purge_task``."""
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    survivor = client.post(f"/api/projects/{pid}/tasks", json={"title": "A"}).json()[
        "id"
    ]
    blocker = client.post(f"/api/projects/{pid}/tasks", json={"title": "B"}).json()[
        "id"
    ]
    _add_edge(client, survivor, blocker)
    assert client.delete(f"/api/tasks/{blocker}").status_code == 204

    resp = client.post(
        "/api/trash/purge", json={"project_ids": [], "task_ids": [blocker]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"projects": 0, "tasks": 1}

    events = _events_for(db_session, survivor)
    assert [e.action for e in events] == [
        "created",
        "dependency_added",
        "dependency_removed",
    ]
    assert events[-1].summary == 'Task "A" no longer waits on permanently deleted "B"'


def test_empty_trash_audits_the_surviving_dependent(
    client: TestClient, db_session: Session
) -> None:
    """Same for the nuclear option: A is still active, so it must be told."""
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    survivor = client.post(f"/api/projects/{pid}/tasks", json={"title": "A"}).json()[
        "id"
    ]
    blocker = client.post(f"/api/projects/{pid}/tasks", json={"title": "B"}).json()[
        "id"
    ]
    _add_edge(client, survivor, blocker)
    assert client.delete(f"/api/tasks/{blocker}").status_code == 204

    resp = client.delete("/api/trash")
    assert resp.status_code == 200
    assert resp.json() == {"projects": 0, "tasks": 1}

    events = _events_for(db_session, survivor)
    assert [e.action for e in events] == [
        "created",
        "dependency_added",
        "dependency_removed",
    ]
    assert events[-1].summary == 'Task "A" no longer waits on permanently deleted "B"'


def test_project_purge_audits_a_dependent_in_another_project(
    client: TestClient, db_session: Session
) -> None:
    """``purge_project`` reaches ``purge_task``, so it audits too.

    The event is filed against the *survivor's* project — the only feed where a
    task that still exists can show it. The purged project's own feed is about to
    have its ``project_id`` references cleared anyway.
    """
    doomed = client.post("/api/projects", json={"name": "Doomed"}).json()["id"]
    keeper = client.post("/api/projects", json={"name": "Keeper"}).json()["id"]
    blocker = client.post(
        f"/api/projects/{doomed}/tasks", json={"title": "B"}
    ).json()["id"]
    survivor = client.post(
        f"/api/projects/{keeper}/tasks", json={"title": "A"}
    ).json()["id"]
    _add_edge(client, survivor, blocker)

    assert client.delete(f"/api/projects/{doomed}").status_code == 204
    assert client.delete(f"/api/projects/{doomed}/purge").status_code == 204

    db_session.expire_all()
    assert db_session.get(Task, blocker) is None
    events = _events_for(db_session, survivor)
    assert [e.action for e in events] == [
        "created",
        "dependency_added",
        "dependency_removed",
    ]
    assert events[-1].project_id == keeper
    assert events[-1].summary == 'Task "A" no longer waits on permanently deleted "B"'


def test_an_edge_between_two_purged_rows_gets_no_event(
    client: TestClient, db_session: Session
) -> None:
    """Nobody survives that edge, so nobody is told about it.

    Both endpoints are inside one purge set — a checklist whose second step waits
    on the first, purged with its parent — and the pair's history is already
    complete: a ``purged`` event each.
    """
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    parent = client.post(f"/api/projects/{pid}/tasks", json={"title": "P"}).json()["id"]
    first = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "C1", "parent_task_id": parent}
    ).json()["id"]
    second = client.post(
        f"/api/projects/{pid}/tasks", json={"title": "C2", "parent_task_id": parent}
    ).json()["id"]
    _add_edge(client, second, first)

    assert client.delete(f"/api/tasks/{parent}").status_code == 204  # cascades
    assert client.delete(f"/api/tasks/{parent}/purge").status_code == 204

    db_session.expire_all()
    assert db_session.get(Task, first) is None
    assert db_session.get(Task, second) is None
    assert _dependency_removed_events(db_session) == []
    assert _actions_for(db_session, second) == [
        "created",
        "dependency_added",
        "deleted",
        "purged",
    ]
    assert _actions_for(db_session, first) == ["created", "deleted", "purged"]


def test_bulk_purge_of_both_endpoints_audits_the_one_still_standing(
    client: TestClient, db_session: Session
) -> None:
    """A bulk purge is a sequence of purges, and each one audits what it leaves.

    Selecting both endpoints purges them one at a time, so whichever goes first
    severs the edge from a task that is — at that moment — still in the trash and
    still restorable. That is the same situation as purging them in two separate
    requests, and #201 already settled that a trashed survivor gets the event. So
    exactly one edge event is written, not zero and not two, and the row it names
    is then purged in its turn.
    """
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    dependent = client.post(f"/api/projects/{pid}/tasks", json={"title": "A"}).json()[
        "id"
    ]
    blocker = client.post(f"/api/projects/{pid}/tasks", json={"title": "B"}).json()[
        "id"
    ]
    _add_edge(client, dependent, blocker)
    assert client.delete(f"/api/tasks/{dependent}").status_code == 204
    assert client.delete(f"/api/tasks/{blocker}").status_code == 204

    resp = client.post(
        "/api/trash/purge", json={"project_ids": [], "task_ids": [dependent, blocker]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"projects": 0, "tasks": 2}

    severed = _dependency_removed_events(db_session)
    assert len(severed) == 1
    assert "permanently deleted" in severed[0].summary
    # Whichever endpoint was audited, it is gone now and its own purge event is
    # the last word on it.
    assert _actions_for(db_session, severed[0].entity_id)[-1] == "purged"


def test_an_already_removed_edge_is_not_audited_twice(
    client: TestClient, db_session: Session
) -> None:
    """A soft-deleted edge was audited when the user removed it (#201).

    Destroying its tombstone in the purge is bookkeeping, not a second removal.
    """
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    survivor = client.post(f"/api/projects/{pid}/tasks", json={"title": "A"}).json()[
        "id"
    ]
    blocker = client.post(f"/api/projects/{pid}/tasks", json={"title": "B"}).json()[
        "id"
    ]
    edge = _add_edge(client, survivor, blocker)

    assert (
        client.delete(f"/api/tasks/{survivor}/dependencies/{edge}").status_code == 204
    )
    assert client.delete(f"/api/tasks/{blocker}").status_code == 204
    assert client.delete(f"/api/tasks/{blocker}/purge").status_code == 204

    events = _events_for(db_session, survivor)
    assert [e.action for e in events] == [
        "created",
        "dependency_added",
        "dependency_removed",
    ]
    # The one event is the manual removal, not a purge event: B was still around
    # to be named when the user removed the edge.
    assert events[-1].summary == 'Task "A" no longer waits on "B"'


def test_a_trashed_survivor_is_audited_too(
    client: TestClient, db_session: Session
) -> None:
    """The survivor need not be active — only restorable.

    A is in the trash on its own terms and is not part of B's purge set, so it can
    still come back, and when it does the edge will be gone. #201 made the same
    call for the mirror case (removing an edge whose blocker is trashed).
    """
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    survivor = client.post(f"/api/projects/{pid}/tasks", json={"title": "A"}).json()[
        "id"
    ]
    blocker = client.post(f"/api/projects/{pid}/tasks", json={"title": "B"}).json()[
        "id"
    ]
    _add_edge(client, survivor, blocker)
    assert client.delete(f"/api/tasks/{survivor}").status_code == 204
    assert client.delete(f"/api/tasks/{blocker}").status_code == 204

    assert client.delete(f"/api/tasks/{blocker}/purge").status_code == 204

    db_session.expire_all()
    still_there = db_session.get(Task, survivor)
    assert still_there is not None and still_there.deleted_at is not None
    assert _actions_for(db_session, survivor) == [
        "created",
        "dependency_added",
        "deleted",
        "dependency_removed",
    ]


def test_an_unfiled_survivor_is_audited_with_no_project(
    client: TestClient, db_session: Session
) -> None:
    """Unfiled tasks are skipped by the ordinary feed helpers, not by this one.

    ``log_task_event`` and ``_log_dependency_event`` stay quiet for a task with no
    project because no feed can show the row — a noise trade-off for reversible
    edits. A purge is not reversible: this is the only surviving record that the
    edge existed, so it is written with a NULL ``project_id`` like the ``purged``
    and detach events.

    ``create_task`` files every new task into General, so the row is unfiled here
    by hand — that shape only survives as legacy data now, but every purge helper
    still guards for it and this one deliberately guards the other way.
    """
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    survivor = client.post(f"/api/projects/{pid}/tasks", json={"title": "A"}).json()[
        "id"
    ]
    blocker = client.post(f"/api/projects/{pid}/tasks", json={"title": "B"}).json()[
        "id"
    ]
    _add_edge(client, survivor, blocker)
    db_session.expire_all()
    unfiled = db_session.get(Task, survivor)
    assert unfiled is not None
    unfiled.project_id = None
    db_session.commit()

    assert client.delete(f"/api/tasks/{blocker}").status_code == 204
    assert client.delete(f"/api/tasks/{blocker}/purge").status_code == 204

    events = _events_for(db_session, survivor)
    assert [e.action for e in events] == [
        "created",
        "dependency_added",
        "dependency_removed",
    ]
    assert events[-1].project_id is None
    assert events[-1].summary == 'Task "A" no longer waits on permanently deleted "B"'


def test_the_severed_edge_event_carries_the_actor(
    client: TestClient, db_session: Session
) -> None:
    """Attribution comes from the shared ``record_event`` actor contextvar."""
    pid = client.post("/api/projects", json={"name": "Ops"}).json()["id"]
    survivor = client.post(f"/api/projects/{pid}/tasks", json={"title": "A"}).json()[
        "id"
    ]
    blocker = client.post(f"/api/projects/{pid}/tasks", json={"title": "B"}).json()[
        "id"
    ]
    _add_edge(client, survivor, blocker)
    assert client.delete(f"/api/tasks/{blocker}").status_code == 204

    db_session.expire_all()
    trashed = task_trash.get_deleted_task(db_session, blocker)
    assert trashed is not None
    token = activity.current_actor.set("agent:mcp")
    try:
        task_trash.purge_task(db_session, trashed)
        db_session.commit()
    finally:
        activity.current_actor.reset(token)

    severed = _events_for(db_session, survivor)[-1]
    assert severed.action == "dependency_removed"
    assert severed.actor == "agent:mcp"
