"""Cascade restore reconciles recurrence once, at the end (issue #199).

``task_trash.restore_task_subtree`` used to restore the root first and its
descendants afterwards, each through ``restore_task``'s own reconciliation. For
a stored-done recurring root with an open child, the root's reconcile ran while
the child was still deleted — the roll-up saw a childless done task and advanced
the series. The checklist then came back open behind a successor that should
never have existed.
"""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import task_trash
from app.services import tasks as tasks_service


def _live_series(db: Session, recurrence_id: str) -> Sequence[Task]:
    return (
        db.execute(
            select(Task)
            .where(Task.recurrence_id == recurrence_id, Task.deleted_at.is_(None))
            .order_by(Task.due_date)
        )
        .scalars()
        .all()
    )


def test_cascade_restore_does_not_advance_an_incomplete_checklist(
    db_session: Session,
) -> None:
    project = projects_service.create_project(db_session, name="Chores")

    # 1. weekly recurring task due 2026-07-26
    root = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="weekly review",
        due_date=date(2026, 7, 26),
    )
    tasks_service.update_task(
        db_session, root, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = root.recurrence_id
    assert recurrence_id is not None
    root_id = root.id

    # 2. complete it: the 2026-08-02 successor appears
    tasks_service.mark_done(db_session, root)
    db_session.commit()
    successor = _live_series(db_session, recurrence_id)[-1]
    assert successor.due_date == date(2026, 8, 2)
    successor_id = successor.id

    # 3. add an open subtask: root stays stored-done, effectively open
    child = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="tidy inbox",
        parent_task_id=root_id,
    )
    db_session.commit()
    child_id = child.id
    assert root.workflow_status is TaskWorkflowStatus.done
    assert (
        deps_service.effective_statuses(db_session, [root_id])[root_id]
        is TaskWorkflowStatus.open
    )

    # 4. trash and purge the existing successor
    tasks_service.soft_delete_task(db_session, successor)
    db_session.commit()
    trashed_successor = task_trash.get_deleted_task(db_session, successor_id)
    assert trashed_successor is not None
    task_trash.purge_task(db_session, trashed_successor)
    db_session.commit()
    assert [t.id for t in _live_series(db_session, recurrence_id)] == [root_id]

    # 5. trash the root, cascading to the open subtask
    tasks_service.soft_delete_task(db_session, root)
    db_session.commit()
    assert task_trash.get_deleted_task(db_session, child_id) is not None

    # 6. restore the root with its subtasks
    trashed_root = task_trash.get_deleted_task(db_session, root_id)
    assert trashed_root is not None
    restored, restored_count = task_trash.restore_task_subtree(
        db_session, trashed_root
    )
    db_session.commit()

    assert restored.id == root_id
    assert restored_count == 1
    assert task_trash.get_deleted_task(db_session, child_id) is None
    # The checklist is open again, so the series must NOT have advanced.
    assert (
        deps_service.effective_statuses(db_session, [root_id])[root_id]
        is TaskWorkflowStatus.open
    )
    assert [t.id for t in _live_series(db_session, recurrence_id)] == [root_id]


def test_cascade_restore_still_respawns_when_the_subtree_is_done(
    db_session: Session,
) -> None:
    """Deferring the reconcile must not lose it: a fully done subtree advances."""
    project = projects_service.create_project(db_session, name="Chores")
    root = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="weekly review",
        due_date=date(2026, 7, 26),
    )
    tasks_service.update_task(
        db_session, root, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    child = tasks_service.create_task(
        db_session,
        project_id=project.id,
        title="tidy inbox",
        parent_task_id=root.id,
    )
    tasks_service.mark_done(db_session, child)
    db_session.commit()
    recurrence_id = root.recurrence_id
    assert recurrence_id is not None
    root_id = root.id

    # The completed checklist already spawned 2026-08-02; purge it so the series
    # has nothing live but the root.
    successor = _live_series(db_session, recurrence_id)[-1]
    assert successor.due_date == date(2026, 8, 2)
    tasks_service.soft_delete_task(db_session, successor)
    db_session.commit()
    trashed_successor = task_trash.get_deleted_task(db_session, successor.id)
    assert trashed_successor is not None
    task_trash.purge_task(db_session, trashed_successor)
    db_session.commit()

    tasks_service.soft_delete_task(db_session, root)
    db_session.commit()
    trashed_root = task_trash.get_deleted_task(db_session, root_id)
    assert trashed_root is not None
    task_trash.restore_task_subtree(db_session, trashed_root)
    db_session.commit()

    assert [t.due_date for t in _live_series(db_session, recurrence_id)] == [
        date(2026, 7, 26),
        date(2026, 8, 2),
    ]
