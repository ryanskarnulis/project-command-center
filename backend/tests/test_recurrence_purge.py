"""Recurrence invariants across the purge (permanent-delete) path — issue #159.

Purge is reachable only from the user-facing Trash (the agent never hard-deletes,
per CLAUDE.md prime directive 2). The question these tests pin is whether purging
a trashed row can change a *live* task's effective completion without recurrence
being reconciled — the #145 invariant break, on the one mutation path PR #155 did
not touch.

It cannot: a trashed row is already excluded from every derived computation
(``get_rollup`` counts active children only, ``is_blocked`` ignores trashed
blockers), and ``soft_delete_task`` already reconciles from the surviving seeds
when the row goes to trash. Purging it therefore removes a row that no live
task's effective status depends on. These tests hold that line so a future change
to purge — or to the roll-up's treatment of trashed rows — can't silently stall a
series.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import task_recurrence, task_trash
from app.services import tasks as tasks_service


def _effective(db: Session, task: Task) -> TaskWorkflowStatus:
    return tasks_service.capped_status(
        tasks_service.get_rollup(db, task).workflow_status,
        deps_service.is_blocked(db, task.id),
    )


def _live_series(db: Session, recurrence_id: str) -> list[Task]:
    return [
        t
        for t in task_recurrence.get_series(db, recurrence_id)
        if t.deleted_at is None
    ]


def _recurring_parent_with_children(
    db: Session, n: int = 2
) -> tuple[Task, list[Task], str]:
    """A weekly recurring parent (due 06-01) with ``n`` open children."""
    project = projects_service.create_project(db, name="Recurring purge")
    parent = tasks_service.create_task(
        db, project_id=project.id, title="weekly checklist", due_date=date(2026, 6, 1)
    )
    db.commit()
    tasks_service.update_task(
        db, parent, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    recurrence_id = parent.recurrence_id
    assert recurrence_id is not None
    children = [
        tasks_service.create_task(
            db, project_id=project.id, parent_task_id=parent.id, title=f"c{i}"
        )
        for i in range(n)
    ]
    db.commit()
    return parent, children, recurrence_id


def test_purging_trashed_last_open_child_leaves_series_with_open_successor(
    db_session: Session,
) -> None:
    # The #159 sequence: live recurring checklist parent, one child done, the last
    # open child trashed and then purged. The successor is spawned when the child
    # is trashed (that is the write that changes effective completion); the purge
    # must neither undo it nor leave the series stalled.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=2)
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()

    tasks_service.soft_delete_task(db_session, children[1])
    db_session.commit()

    trashed = task_trash.get_deleted_task(db_session, children[1].id)
    assert trashed is not None
    task_trash.purge_task(db_session, trashed)
    db_session.commit()

    assert db_session.get(Task, children[1].id) is None
    assert _effective(db_session, parent) == TaskWorkflowStatus.done

    live = _live_series(db_session, recurrence_id)
    assert [t.due_date for t in live] == [date(2026, 6, 1), date(2026, 6, 8)]
    successor = live[-1]
    assert successor.id != parent.id
    assert successor.workflow_status == TaskWorkflowStatus.open


def test_purging_trashed_done_child_keeps_single_successor(
    db_session: Session,
) -> None:
    # The other direction: the child that completed the series is trashed and then
    # purged after the successor exists. Purge must not spawn a second occurrence
    # for the same cadence (``create_next_occurrence`` is idempotent, and purge has
    # no business advancing a series at all).
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=1)
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()
    assert len(_live_series(db_session, recurrence_id)) == 2

    tasks_service.soft_delete_task(db_session, children[0])
    db_session.commit()

    trashed = task_trash.get_deleted_task(db_session, children[0].id)
    assert trashed is not None
    task_trash.purge_task(db_session, trashed)
    db_session.commit()

    assert db_session.get(Task, children[0].id) is None
    assert [t.due_date for t in _live_series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
    ]
    assert parent.parent_task_id is None


def test_purging_trashed_blocker_leaves_series_with_open_successor(
    db_session: Session,
) -> None:
    # The other door into effective completion: a blocker. Trashing it unblocks the
    # recurring task; purging it removes the dependency edge too, and must not move
    # the series backwards or spawn a duplicate.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=1)
    blocker = tasks_service.create_task(
        db_session, project_id=parent.project_id, title="blocker"
    )
    db_session.commit()
    deps_service.add_dependency(db_session, task_id=parent.id, depends_on_id=blocker.id)
    db_session.commit()
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()

    tasks_service.soft_delete_task(db_session, blocker)
    db_session.commit()

    trashed = task_trash.get_deleted_task(db_session, blocker.id)
    assert trashed is not None
    task_trash.purge_task(db_session, trashed)
    db_session.commit()

    assert _effective(db_session, parent) == TaskWorkflowStatus.done
    assert [t.due_date for t in _live_series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
    ]


def test_purging_trashed_child_of_open_recurring_parent_spawns_nothing(
    db_session: Session,
) -> None:
    # The negative: with an open sibling still outstanding the parent is not
    # effectively done, so purging the trashed child must not spawn anything.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=2)

    tasks_service.soft_delete_task(db_session, children[1])
    db_session.commit()

    trashed = task_trash.get_deleted_task(db_session, children[1].id)
    assert trashed is not None
    task_trash.purge_task(db_session, trashed)
    db_session.commit()

    assert _effective(db_session, parent) != TaskWorkflowStatus.done
    assert [t.due_date for t in _live_series(db_session, recurrence_id)] == [
        date(2026, 6, 1)
    ]
    rows = (
        db_session.execute(select(Task).where(Task.recurrence_id == recurrence_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1  # no successor row at all, live or trashed
