"""A purge must leave no cascade marker a recycled task id can match (#251).

``tasks.id`` is a plain SQLite rowid, so purging the highest-numbered task frees
its id for the next insert. ``purge_task`` used to clear only ``parent_task_id``
on the rows it spared and leave their ``deleted_with_task_id`` naming the
destroyed root; once an unrelated new task was handed that id,
``_marked_descendant_ids`` matched the stale marker and ``restore_task_subtree``
pulled another project's trashed work into that task's undo.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, Task
from app.services import task_trash


def _trashed_cross_project_child(client: TestClient) -> tuple[int, int, int]:
    """The issue's shape: X owned by Q, parented under T in P, both in the trash.

    X is created *first* so T ends up holding the highest task id — the condition
    that makes purging T free an id the next insert reuses. The reparent is a
    PATCH because a child created under T would inherit T's project, and the
    scoped purge walk only spares a descendant owned by a different project.

    Returns ``(project Q, task X, task T)``.
    """
    q = client.post("/api/projects", json={"name": "Project Q"}).json()["id"]
    p = client.post("/api/projects", json={"name": "Project P"}).json()["id"]
    x = client.post(f"/api/projects/{q}/tasks", json={"title": "X"}).json()["id"]
    t = client.post(f"/api/projects/{p}/tasks", json={"title": "T"}).json()["id"]
    assert client.patch(f"/api/tasks/{x}", json={"parent_task_id": t}).status_code == 200
    assert client.delete(f"/api/tasks/{t}").status_code == 204  # cascades onto X
    return q, x, t


def _recycle_purged_id(client: TestClient, purged_id: int) -> int:
    """Purge ``purged_id``, then create a task and assert it inherited that id."""
    assert client.delete(f"/api/tasks/{purged_id}/purge").status_code == 204
    new_id: int = client.post("/api/tasks", json={"title": "N"}).json()["id"]
    # The bug needs the recycled id; without it the test proves nothing.
    assert new_id == purged_id, "SQLite did not reuse the purged rowid — repro invalid"
    return new_id


def _event_summaries(db: Session, task_id: int) -> list[str]:
    """Every recorded event summary for one task, oldest first."""
    return list(
        db.execute(
            select(ActivityEvent.summary)
            .where(
                ActivityEvent.entity_type == "task",
                ActivityEvent.entity_id == task_id,
            )
            .order_by(ActivityEvent.id)
        ).scalars()
    )


def test_purge_clears_the_cascade_marker_of_a_child_it_spares(
    client: TestClient, db_session: Session
) -> None:
    """The spared row is left with nothing pointing at the destroyed root."""
    _, x, t = _trashed_cross_project_child(client)
    db_session.expire_all()
    marked = db_session.get(Task, x)
    assert marked is not None
    assert marked.deleted_with_task_id == t  # stamped by T's cascade
    before = _event_summaries(db_session, x)

    assert client.delete(f"/api/tasks/{t}/purge").status_code == 204

    db_session.expire_all()
    assert db_session.get(Task, t) is None
    survivor = db_session.get(Task, x)
    assert survivor is not None
    assert survivor.deleted_at is not None  # spared: still restorable on its own
    assert survivor.parent_task_id is None  # detached (#242)
    assert survivor.deleted_with_task_id is None  # and unmarked (#251)

    # Clearing the marker is internal restore bookkeeping, not a change to
    # anything the user can see, so it is silent — like
    # ``projects.purge_project``'s null-out of ``deleted_with_project_id``. The
    # detach is the only event this purge writes about the survivor (#242).
    assert _event_summaries(db_session, x) == [
        *before,
        'Task "X" detached from permanently deleted parent "T"',
    ]


def test_subtree_restore_of_a_recycled_id_reports_no_descendants(
    client: TestClient, db_session: Session
) -> None:
    """``restore_task_subtree`` counts what *this* task's delete removed: nothing.

    The count is the service's return value, not part of the route's response
    body, so the assertion is made where it is produced.
    """
    _, x, t = _trashed_cross_project_child(client)
    new_id = _recycle_purged_id(client, t)
    assert client.delete(f"/api/tasks/{new_id}").status_code == 204

    recycled = task_trash.get_deleted_task(db_session, new_id)
    assert recycled is not None
    _, restored_subtask_count = task_trash.restore_task_subtree(db_session, recycled)
    assert restored_subtask_count == 0

    foreign = db_session.get(Task, x)
    assert foreign is not None
    assert foreign.deleted_at is not None


def test_restore_with_subtasks_leaves_another_projects_trash_alone(
    client: TestClient, db_session: Session
) -> None:
    """The same undo over HTTP — the agent's documented Undo for ``trash_task``.

    ``restore_task_subtree`` promises "exactly the descendants trashed with it and
    nothing else". A task inheriting a purged id inherits nothing else with it,
    least of all a cascade that belonged to another project's delete.
    """
    q, x, t = _trashed_cross_project_child(client)
    new_id = _recycle_purged_id(client, t)
    assert client.delete(f"/api/tasks/{new_id}").status_code == 204

    restored = client.post(f"/api/tasks/{new_id}/restore?restore_subtasks=true")
    assert restored.status_code == 200
    assert restored.json()["id"] == new_id

    db_session.expire_all()
    foreign = db_session.get(Task, x)
    assert foreign is not None
    assert foreign.deleted_at is not None, "another project's trashed task was restored"
    assert foreign.project_id == q
    # Still its own standalone trash entry, restorable by the user who trashed it
    # — on their say-so, not somebody else's undo.
    assert x in [row["id"] for row in client.get("/api/trash").json()["tasks"]]
    # And no ``restored`` event was attributed to that unrelated undo.
    restored_ids = (
        db_session.execute(
            select(ActivityEvent.entity_id).where(
                ActivityEvent.entity_type == "task",
                ActivityEvent.action == "restored",
            )
        )
        .scalars()
        .all()
    )
    assert list(restored_ids) == [new_id]
