"""Un-skipping a recurring checklist with a cross-project child — issue #212.

``skip_occurrence`` clones the subtree (children keep their own ``project_id``)
and soft-deletes the original. Un-skipping rewinds the live replacement and
purges the original — but the purge walk is project-scoped (BUG #189), so a
child filed in another project used to be merely *detached* and left behind in
standalone trash, an obsolete duplicate of its live clone.

The un-skip purge now opts out of that scoping; the user-facing purge keeps it
(see ``test_routes_trash.py`` for the #189 coverage).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.db.models import Task
from app.services import projects as projects_service
from app.services import task_recurrence, task_trash
from app.services import tasks as tasks_service


def test_unskip_purges_the_whole_original_occurrence_across_projects(
    db_session: Session,
) -> None:
    project_a = projects_service.create_project(db_session, name="A")
    project_b = projects_service.create_project(db_session, name="B")
    parent = tasks_service.create_task(
        db_session,
        project_id=project_a.id,
        title="weekly checklist",
        due_date=date(2026, 7, 1),
    )
    db_session.commit()
    tasks_service.update_task(
        db_session, parent, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = parent.recurrence_id
    assert recurrence_id is not None

    child = tasks_service.create_task(
        db_session,
        project_id=project_b.id,
        parent_task_id=parent.id,
        title="cross-project step",
    )
    db_session.commit()
    original_parent_id, original_child_id = parent.id, child.id

    task_recurrence.skip_occurrence(db_session, parent)
    db_session.commit()

    skipped = task_trash.get_deleted_task(db_session, original_parent_id)
    assert skipped is not None
    live = task_trash.restore_task(db_session, skipped)
    db_session.commit()

    # The series is rewound to the un-skipped date, with exactly one occurrence.
    series = [
        t
        for t in task_recurrence.get_series(db_session, recurrence_id)
        if t.deleted_at is None
    ]
    assert [t.id for t in series] == [live.id]
    assert live.due_date == date(2026, 7, 1)
    assert live.id != original_parent_id

    # Exactly one live child: the clone, still filed in project B.
    live_children = [
        t
        for t in db_session.query(Task).filter(
            Task.parent_task_id == live.id, Task.deleted_at.is_(None)
        )
    ]
    assert len(live_children) == 1
    assert live_children[0].project_id == project_b.id
    assert live_children[0].id != original_child_id

    # Both originals are gone entirely — not merely detached into trash.
    assert db_session.get(Task, original_parent_id) is None
    assert db_session.get(Task, original_child_id) is None
    trashed_ids = {t.id for t in task_trash.list_deleted_tasks(db_session)}
    assert original_child_id not in trashed_ids
    assert original_parent_id not in trashed_ids

    # And no obsolete detached leftovers anywhere.
    all_tasks = db_session.query(Task).all()
    assert {t.id for t in all_tasks} == {live.id, live_children[0].id}
