from collections.abc import Sequence
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency, TaskWorkflowStatus
from app.schemas.tasks import RepeatInterval
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
from app.services import task_recurrence, task_trash
from app.services import tasks as tasks_service


# --- Schema (Chunk B) -------------------------------------------------------


def test_repeat_interval_serializes_both_fields() -> None:
    assert RepeatInterval(unit="week", every=2).model_dump() == {
        "unit": "week",
        "every": 2,
    }


@pytest.mark.parametrize("bad", [0, 13, -1, 100])
def test_repeat_interval_rejects_out_of_range_every(bad: int) -> None:
    with pytest.raises(ValidationError):
        RepeatInterval(unit="day", every=bad)


def test_repeat_interval_rejects_unknown_unit() -> None:
    with pytest.raises(ValidationError):
        RepeatInterval(unit="year", every=1)  # type: ignore[arg-type]


# --- Helpers ----------------------------------------------------------------


def _make_task(db: Session, *, due: date | None) -> Task:
    project = projects_service.create_project(db, name="Recurring")
    task = tasks_service.create_task(
        db, project_id=project.id, title="water plants", due_date=due
    )
    db.commit()
    return task


def _series(db: Session, recurrence_id: str) -> Sequence[Task]:
    return (
        db.execute(
            select(Task)
            .where(Task.recurrence_id == recurrence_id, Task.deleted_at.is_(None))
            .order_by(Task.due_date)
        )
        .scalars()
        .all()
    )


def _active_count(db: Session) -> int:
    return len(
        db.execute(select(Task).where(Task.deleted_at.is_(None))).scalars().all()
    )


# --- Service behavior (Chunk C) ---------------------------------------------


def test_complete_non_recurring_creates_no_new_task(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    before = _active_count(db_session)

    tasks_service.update_task(
        db_session, task, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()

    assert _active_count(db_session) == before


def test_setting_repeat_first_time_mints_recurrence_id(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    assert task.recurrence_id is None

    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    assert task.repeat_interval == {"unit": "week", "every": 1}
    assert task.recurrence_id is not None


def test_complete_recurring_creates_next_occurrence(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))  # a Monday
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    tasks_service.update_task(
        db_session, task, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()

    series = _series(db_session, recurrence_id)
    assert len(series) == 2
    occurrence = series[-1]
    assert occurrence.id != task.id
    assert occurrence.due_date == date(2026, 6, 8)
    assert occurrence.recurrence_id == recurrence_id
    assert occurrence.workflow_status == TaskWorkflowStatus.open
    assert occurrence.parent_task_id is None
    assert occurrence.repeat_interval == {"unit": "week", "every": 1}


def test_mark_done_creates_next_occurrence(db_session: Session) -> None:
    # The task lists/cards complete via mark_done (POST /tasks/{id}/done), not the
    # detail page's PATCH. That path must spawn the next occurrence too.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    tasks_service.mark_done(db_session, task)
    db_session.commit()

    series = _series(db_session, recurrence_id)
    assert len(series) == 2
    occurrence = series[-1]
    assert occurrence.id != task.id
    assert occurrence.due_date == date(2026, 6, 8)
    assert occurrence.workflow_status == TaskWorkflowStatus.open


def test_mark_done_non_recurring_creates_no_new_task(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    before = _active_count(db_session)

    tasks_service.mark_done(db_session, task)
    db_session.commit()

    assert _active_count(db_session) == before


def test_skip_soft_deletes_current_and_rolls_forward(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    next_occurrence = task_recurrence.skip_occurrence(db_session, task)
    db_session.commit()

    # The skipped occurrence is soft-deleted (recoverable), not marked done.
    assert task.deleted_at is not None
    assert task.workflow_status != TaskWorkflowStatus.done
    # The series rolls forward to a fresh open occurrence one interval later.
    series = _series(db_session, recurrence_id)
    assert len(series) == 1
    assert series[0].id == next_occurrence.id
    assert next_occurrence.due_date == date(2026, 6, 8)
    assert next_occurrence.workflow_status == TaskWorkflowStatus.open


# --- next_occurrence_date on the read payload (Slice 2) ---------------------


def test_next_occurrence_date_advances_open_recurring(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    assert task_recurrence.next_occurrence_date(task) == date(2026, 6, 8)


def test_next_occurrence_date_none_without_interval(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))

    assert task_recurrence.next_occurrence_date(task) is None


def test_next_occurrence_date_none_when_done(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    # Completing spawns the successor and marks this row done — no "next" here.
    tasks_service.update_task(
        db_session, task, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()

    assert task_recurrence.next_occurrence_date(task) is None


def test_task_read_exposes_next_occurrence_date(db_session: Session) -> None:
    from app.api.task_reads import read_with_blocked

    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    read = read_with_blocked(db_session, task)
    assert read.next_occurrence_date == date(2026, 6, 8)

    plain = _make_task(db_session, due=date(2026, 6, 1))
    assert read_with_blocked(db_session, plain).next_occurrence_date is None


def test_skip_non_recurring_task_raises(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))

    with pytest.raises(tasks_service.RecurrenceError):
        task_recurrence.skip_occurrence(db_session, task)


def test_clearing_repeat_stops_future_occurrences(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    tasks_service.update_task(db_session, task, {"repeat_interval": None})
    db_session.commit()
    # recurrence_id is intentionally left intact so the chain stays readable.
    assert task.recurrence_id is not None
    before = _active_count(db_session)

    tasks_service.update_task(
        db_session, task, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()

    assert _active_count(db_session) == before


def test_month_interval_clamps_to_short_month(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 1, 31))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "month", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    tasks_service.update_task(
        db_session, task, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()

    occurrence = _series(db_session, recurrence_id)[-1]
    assert occurrence.due_date == date(2026, 2, 28)


def _three_occurrence_series(db_session: Session) -> tuple[str, Sequence[Task]]:
    """A weekly series: due 06-01 (done), 06-08 (done), 06-15 (open)."""
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    tasks_service.update_task(
        db_session, task, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()
    second = _series(db_session, recurrence_id)[-1]
    tasks_service.update_task(
        db_session, second, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()
    return recurrence_id, _series(db_session, recurrence_id)


def test_edit_scope_future_patches_forward_rows_only(db_session: Session) -> None:
    recurrence_id, series = _three_occurrence_series(db_session)
    first, second, third = series
    assert [t.due_date for t in series] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
        date(2026, 6, 15),
    ]

    tasks_service.update_task(
        db_session, second, {"title": "deep clean", "edit_scope": "future"}
    )
    db_session.commit()

    refreshed = _series(db_session, recurrence_id)
    titles = {t.due_date: t.title for t in refreshed}
    assert titles[date(2026, 6, 1)] == "water plants"  # past row untouched
    assert titles[date(2026, 6, 8)] == "deep clean"
    assert titles[date(2026, 6, 15)] == "deep clean"


def test_edit_scope_this_patches_only_target_row(db_session: Session) -> None:
    recurrence_id, series = _three_occurrence_series(db_session)
    second = series[1]

    tasks_service.update_task(
        db_session, second, {"title": "deep clean", "edit_scope": "this"}
    )
    db_session.commit()

    refreshed = _series(db_session, recurrence_id)
    titles = {t.due_date: t.title for t in refreshed}
    assert titles[date(2026, 6, 1)] == "water plants"
    assert titles[date(2026, 6, 8)] == "deep clean"
    assert titles[date(2026, 6, 15)] == "water plants"  # future row untouched


def test_edit_scope_future_does_not_forward_structural_fields(
    db_session: Session,
) -> None:
    # A forward-patch must not bulk-propagate structural fields (parent_task_id,
    # project_id): the bulk UPDATE skips the cycle / derived-status /
    # project-coupling guards, so a crafted "future" patch setting parent_task_id
    # onto the acted-on row would otherwise make the future occurrence its own
    # parent. Only the acted-on row takes the (guarded) edit.
    recurrence_id, series = _three_occurrence_series(db_session)
    _first, second, third = series

    tasks_service.update_task(
        db_session, second, {"parent_task_id": third.id, "edit_scope": "future"}
    )
    db_session.commit()

    refreshed = {t.due_date: t for t in _series(db_session, recurrence_id)}
    # The acted-on row took the guarded edit...
    assert refreshed[date(2026, 6, 8)].parent_task_id == third.id
    # ...but the forward patch did NOT self-parent the future occurrence...
    assert refreshed[date(2026, 6, 15)].parent_task_id is None
    # ...nor touch the past row.
    assert refreshed[date(2026, 6, 1)].parent_task_id is None


def test_setting_repeat_without_due_date_raises(db_session: Session) -> None:
    task = _make_task(db_session, due=None)

    with pytest.raises(tasks_service.RecurrenceError):
        tasks_service.update_task(
            db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
        )


def test_setting_repeat_with_due_date_in_same_request_succeeds(
    db_session: Session,
) -> None:
    task = _make_task(db_session, due=None)

    tasks_service.update_task(
        db_session,
        task,
        {
            "due_date": date(2026, 7, 1),
            "repeat_interval": {"unit": "month", "every": 2},
        },
    )
    db_session.commit()

    assert task.due_date == date(2026, 7, 1)
    assert task.recurrence_id is not None


def test_patch_repeat_without_due_date_returns_422_over_http(
    client: TestClient, db_session: Session
) -> None:
    task = _make_task(db_session, due=None)

    res = client.patch(
        f"/api/tasks/{task.id}",
        json={"repeat_interval": {"unit": "week", "every": 1}},
    )

    assert res.status_code == 422


def test_skip_non_recurring_returns_422_over_http(
    client: TestClient, db_session: Session
) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))

    res = client.post(f"/api/tasks/{task.id}/skip")

    assert res.status_code == 422
    assert res.json()["detail"] == (
        "Only a recurring task with a due date can be skipped"
    )


# --- Series management (Recurring series management slice) -------------------


def test_get_series_includes_skipped_in_due_date_order(db_session: Session) -> None:
    # A weekly series: complete the first (06-01 done, spawns 06-08 open), then
    # skip the second (06-08 soft-deleted, spawns 06-15 open).
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    tasks_service.update_task(
        db_session, task, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()
    second = _series(db_session, recurrence_id)[-1]
    task_recurrence.skip_occurrence(db_session, second)
    db_session.commit()

    series = task_recurrence.get_series(db_session, recurrence_id)
    # All three rows present, including the soft-deleted skipped one, oldest first.
    assert [t.due_date for t in series] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
        date(2026, 6, 15),
    ]
    skipped = next(t for t in series if t.due_date == date(2026, 6, 8))
    assert skipped.deleted_at is not None


def test_restore_skipped_occurrence_unskips_without_duplicating(
    db_session: Session,
) -> None:
    # Skip the first occurrence (06-01 -> soft-deleted, spawns 06-08 open), then
    # restore it. Restore must NOT add a second live row; instead the live
    # occurrence is pulled back to 06-01 and the skipped row is hard-deleted.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None
    skipped_id = task.id

    task_recurrence.skip_occurrence(db_session, task)
    db_session.commit()
    assert len(task_trash.list_deleted_tasks(db_session)) == 1

    restored = task_trash.restore_task(db_session, task)
    db_session.commit()

    # Exactly one live occurrence, due back at the un-skipped date.
    series = _series(db_session, recurrence_id)
    assert len(series) == 1
    assert series[0].id == restored.id
    assert restored.due_date == date(2026, 6, 1)
    # The skipped row is hard-deleted: gone from trash and from the series timeline.
    assert task_trash.list_deleted_tasks(db_session) == []
    assert all(t.id != skipped_id for t in task_recurrence.get_series(db_session, recurrence_id))

    # Completing the restored occurrence spawns exactly one next occurrence: the
    # done 06-01 row stays, plus a single fresh 06-08 open row (no extra duplicate).
    tasks_service.mark_done(db_session, restored)
    db_session.commit()
    after = _series(db_session, recurrence_id)
    assert len(after) == 2
    assert after[-1].due_date == date(2026, 6, 8)
    assert after[-1].workflow_status == TaskWorkflowStatus.open


def test_restore_skipped_checklist_resets_subtasks(db_session: Session) -> None:
    # A recurring checklist: complete all children (spawns the next occurrence with
    # a fresh subtree), skip that occurrence, then restore it. The live occurrence
    # and its whole subtree must reset to the restored date.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session)
    for child in children:
        tasks_service.mark_done(db_session, child)
        db_session.commit()
    second = _series(db_session, recurrence_id)[-1]
    assert second.due_date == date(2026, 6, 8)

    task_recurrence.skip_occurrence(db_session, second)
    db_session.commit()
    skipped_id = second.id

    restored = task_trash.restore_task(db_session, second)
    db_session.commit()

    # The completed parent (06-01) stays as history; the forward occurrence is
    # pulled back to the un-skipped date with its whole subtree reset, and the
    # spawned 06-15 duplicate is gone — exactly one live occurrence at 06-08.
    assert restored.due_date == date(2026, 6, 8)
    live = [
        t
        for t in _series(db_session, recurrence_id)
        if t.due_date is not None and t.due_date >= date(2026, 6, 8)
    ]
    assert [t.id for t in live] == [restored.id]
    clones = tasks_service.list_subtasks(db_session, restored.id)
    assert {c.due_date for c in clones} == {date(2026, 6, 8)}
    assert all(t.id != skipped_id for t in task_recurrence.get_series(db_session, recurrence_id))


def test_restore_non_recurring_task_plain_restore(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    restored = task_trash.restore_task(db_session, task)
    db_session.commit()

    assert restored.id == task.id
    assert restored.deleted_at is None
    assert task_trash.list_deleted_tasks(db_session) == []


def test_restore_recurring_with_no_live_occurrence_plain_restore(
    db_session: Session,
) -> None:
    # A recurring task whose series has no other live occurrence: the duplicate
    # hazard doesn't apply, so restore is a plain un-delete (not a purge).
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    restored = task_trash.restore_task(db_session, task)
    db_session.commit()

    assert restored.id == task.id
    assert restored.deleted_at is None


def test_stop_recurrence_clears_repeat_keeps_id(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    task_recurrence.stop_recurrence(db_session, task)
    db_session.commit()

    assert task.repeat_interval is None
    assert task.recurrence_id == recurrence_id  # chain stays readable
    before = _active_count(db_session)

    # Completing it now spawns no further occurrence.
    tasks_service.mark_done(db_session, task)
    db_session.commit()
    assert _active_count(db_session) == before


def test_stop_recurrence_non_recurring_raises(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))

    with pytest.raises(tasks_service.RecurrenceError):
        task_recurrence.stop_recurrence(db_session, task)


def test_get_series_over_http(client: TestClient, db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    res = client.get(f"/api/tasks/{task.id}/series")
    assert res.status_code == 200
    body = res.json()
    assert body["recurrence_id"] == task.recurrence_id
    assert len(body["occurrences"]) == 1
    assert body["occurrences"][0]["id"] == task.id


def test_get_series_non_recurring_returns_422(
    client: TestClient, db_session: Session
) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))

    res = client.get(f"/api/tasks/{task.id}/series")
    assert res.status_code == 422


# --- Recurring checklist tasks (recurrence + subtasks) ----------------------


def _recurring_parent_with_children(
    db: Session, n: int = 2
) -> tuple[Task, list[Task], str]:
    """A weekly recurring parent (due 06-01) with ``n`` accepted open children."""
    parent = _make_task(db, due=date(2026, 6, 1))
    tasks_service.update_task(
        db, parent, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    recurrence_id = parent.recurrence_id
    assert recurrence_id is not None
    children = [
        tasks_service.create_task(
            db, project_id=parent.project_id, parent_task_id=parent.id, title=f"c{i}"
        )
        for i in range(n)
    ]
    db.commit()
    return parent, children, recurrence_id


def test_partial_completion_does_not_spawn(db_session: Session) -> None:
    _parent, children, recurrence_id = _recurring_parent_with_children(db_session)
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()

    # Only one of two children done -> parent not rolled up to done -> no spawn.
    assert len(_series(db_session, recurrence_id)) == 1


def test_skip_checklist_occurrence_cascades_to_subtree(db_session: Session) -> None:
    # Skipping a recurring checklist occurrence must soft-delete its whole subtree,
    # not just the occurrence row. Otherwise the subtasks stay active pointing at a
    # trashed parent and surface as leaked root-level orphans (buildTaskTree
    # promotes an orphan to a root).
    parent, children, _recurrence_id = _recurring_parent_with_children(db_session)

    next_occurrence = task_recurrence.skip_occurrence(db_session, parent)
    db_session.commit()

    # The occurrence and its whole subtree are soft-deleted together — no active
    # subtask is left orphaned under the trashed parent.
    assert parent.deleted_at is not None
    for child in children:
        assert child.deleted_at is not None
    assert tasks_service.list_subtasks(db_session, parent.id) == []

    # The series still rolled forward, with a freshly-cloned subtree under the new
    # occurrence (the skip cascade doesn't touch the next occurrence's clones).
    assert next_occurrence.due_date == date(2026, 6, 8)
    assert sorted(
        c.title for c in tasks_service.list_subtasks(db_session, next_occurrence.id)
    ) == ["c0", "c1"]


def test_completing_last_child_spawns_checklist_occurrence(
    db_session: Session,
) -> None:
    parent, children, recurrence_id = _recurring_parent_with_children(db_session)
    for child in children:
        tasks_service.mark_done(db_session, child)
        db_session.commit()

    series = _series(db_session, recurrence_id)
    assert len(series) == 2
    occurrence = series[-1]
    assert occurrence.id != parent.id
    assert occurrence.due_date == date(2026, 6, 8)
    assert occurrence.recurrence_id == recurrence_id
    assert occurrence.parent_task_id is None
    assert occurrence.repeat_interval == {"unit": "week", "every": 1}

    # The whole checklist is cloned fresh under the new occurrence: open, no recurrence.
    clones = tasks_service.list_subtasks(db_session, occurrence.id)
    assert sorted(c.title for c in clones) == ["c0", "c1"]
    for clone in clones:
        assert clone.workflow_status == TaskWorkflowStatus.open
        assert clone.repeat_interval is None
        assert clone.recurrence_id is None
        # Clones inherit the new occurrence's due date, not the prior cadence's.
        assert clone.due_date == date(2026, 6, 8)


def test_completing_last_child_via_patch_spawns_occurrence(
    db_session: Session,
) -> None:
    _parent, children, recurrence_id = _recurring_parent_with_children(db_session)
    for child in children:
        tasks_service.update_task(
            db_session, child, {"workflow_status": TaskWorkflowStatus.done}
        )
        db_session.commit()

    assert len(_series(db_session, recurrence_id)) == 2


def test_checklist_clones_grandchildren(db_session: Session) -> None:
    parent = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, parent, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = parent.recurrence_id
    assert recurrence_id is not None
    mid = tasks_service.create_task(
        db_session, project_id=parent.project_id, parent_task_id=parent.id, title="mid"
    )
    leaf = tasks_service.create_task(
        db_session, project_id=parent.project_id, parent_task_id=mid.id, title="leaf"
    )
    db_session.commit()

    tasks_service.mark_done(db_session, leaf)
    db_session.commit()

    occurrence = _series(db_session, recurrence_id)[-1]
    mid_clones = tasks_service.list_subtasks(db_session, occurrence.id)
    assert [c.title for c in mid_clones] == ["mid"]
    leaf_clones = tasks_service.list_subtasks(db_session, mid_clones[0].id)
    assert [c.title for c in leaf_clones] == ["leaf"]
    assert leaf_clones[0].workflow_status == TaskWorkflowStatus.open


def test_recurring_parent_direct_mark_done_still_rejected(
    db_session: Session,
) -> None:
    parent, _children, _rid = _recurring_parent_with_children(db_session)
    with pytest.raises(tasks_service.DerivedStatusError):
        tasks_service.mark_done(db_session, parent)


def test_stop_recurrence_over_http(client: TestClient, db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    res = client.post(f"/api/tasks/{task.id}/stop-recurrence")
    assert res.status_code == 200
    assert res.json()["repeat_interval"] is None


# --- Un-skip with dependencies present (recurrence × dependency seam) --------


def test_unskip_cleans_dependency_edges_on_the_skipped_row(
    db_session: Session,
) -> None:
    # R depends on blocker B. Skipping R soft-deletes it and spawns the next
    # occurrence; the edge stays on the skipped row. Un-skipping purges that row,
    # which must clean its dependency edges — nothing may reference the
    # hard-deleted id (FK enforcement would raise), and the retargeted live
    # occurrence must not inherit a phantom block.
    blocker = _make_task(db_session, due=None)
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    deps_service.add_dependency(db_session, task_id=task.id, depends_on_id=blocker.id)
    db_session.commit()
    skipped_id = task.id

    live = task_recurrence.skip_occurrence(db_session, task)
    db_session.commit()
    # Edges don't carry over to the spawned occurrence.
    assert not deps_service.is_blocked(db_session, live.id)

    restored = task_trash.restore_task(db_session, task)
    db_session.commit()

    # Un-skip retargeted the live occurrence (the skipped row was purged)...
    assert restored.id == live.id
    assert restored.due_date == date(2026, 6, 1)
    # ...and no dependency edge references the purged id on either side.
    edges = db_session.execute(select(TaskDependency)).scalars().all()
    assert all(skipped_id not in (e.task_id, e.depends_on_task_id) for e in edges)
    assert not deps_service.is_blocked(db_session, restored.id)


def test_unskip_with_dependent_task_present(db_session: Session) -> None:
    # T depends on recurring R. Skipping R sends T's blocker to trash (a trashed
    # blocker no longer blocks); un-skipping purges the skipped row and its edge.
    # T must never point at a hard-deleted row or stay phantom-blocked.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    dependent = _make_task(db_session, due=None)
    deps_service.add_dependency(
        db_session, task_id=dependent.id, depends_on_id=task.id
    )
    db_session.commit()
    skipped_id = task.id
    assert deps_service.is_blocked(db_session, dependent.id)

    task_recurrence.skip_occurrence(db_session, task)
    db_session.commit()
    assert not deps_service.is_blocked(db_session, dependent.id)

    restored = task_trash.restore_task(db_session, task)
    db_session.commit()

    edges = db_session.execute(select(TaskDependency)).scalars().all()
    assert all(skipped_id not in (e.task_id, e.depends_on_task_id) for e in edges)
    assert not deps_service.is_blocked(db_session, dependent.id)
    assert restored.due_date == date(2026, 6, 1)
