from collections.abc import Sequence
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency, TaskWorkflowStatus
from app.schemas.tasks import RepeatInterval
from app.services import activity as activity_service
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


def _effective(db: Session, task: Task) -> TaskWorkflowStatus:
    """The task's effective status, the way every read surface resolves it."""
    return tasks_service.capped_status(
        tasks_service.get_rollup(db, task).workflow_status,
        deps_service.is_blocked(db, task.id),
    )


def _next_date(db: Session, task: Task) -> date | None:
    return task_recurrence.next_occurrence_date(task, _effective(db, task))


def _live_series(db: Session, recurrence_id: str) -> list[Task]:
    """The series' active rows only, oldest first — the uniqueness surface."""
    return [t for t in task_recurrence.get_series(db, recurrence_id) if t.deleted_at is None]


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


# --- Idempotent successor creation -------------------------------------------


def test_reopen_and_recomplete_does_not_duplicate_occurrence(
    db_session: Session,
) -> None:
    # Completion is not a once-only event: reopening a done occurrence and
    # completing it again makes the open->done transition a second time. The
    # successor is resolved by (recurrence_id, due date), so the re-completion
    # finds 06-08 and returns it instead of inserting a twin.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    tasks_service.mark_done(db_session, task)
    db_session.commit()
    first = _series(db_session, recurrence_id)[-1]

    tasks_service.reopen_task(db_session, task)
    db_session.commit()
    tasks_service.mark_done(db_session, task)
    db_session.commit()

    series = _series(db_session, recurrence_id)
    assert len(series) == 2
    # Reopening leaves the already-spawned successor alone rather than trashing it,
    # so it must be the same row, not a replacement.
    assert [t.id for t in series] == [task.id, first.id]


def test_recomplete_does_not_respawn_a_skipped_occurrence(db_session: Session) -> None:
    # A skipped occurrence is soft-deleted but still happened as a scheduling
    # decision, so the successor guard counts it: re-completing the predecessor
    # must not resurrect a date the user explicitly said didn't happen.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    tasks_service.mark_done(db_session, task)
    db_session.commit()
    spawned = _series(db_session, recurrence_id)[-1]
    task_recurrence.skip_occurrence(db_session, spawned)  # 06-08 skipped -> 06-15
    db_session.commit()

    tasks_service.reopen_task(db_session, task)
    db_session.commit()
    tasks_service.mark_done(db_session, task)
    db_session.commit()

    # 06-08 stays skipped; the live series is the original plus 06-15.
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 15),
    ]
    assert spawned.deleted_at is not None


# --- Skip rolls forward to a live occurrence (BUG-2) -------------------------


def _two_live_occurrences(db_session: Session) -> tuple[str, Task, Task]:
    """A weekly series left with two live open occurrences: 06-01 and 06-08.

    Completing 06-01 spawns 06-08; reopening 06-01 leaves 06-08 alone, so both are
    open and live at once. This is the precondition where skip's next date can
    already hold another row (trashed or skipped).
    """
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None
    tasks_service.mark_done(db_session, task)
    db_session.commit()
    second = _series(db_session, recurrence_id)[-1]
    tasks_service.reopen_task(db_session, task)
    db_session.commit()
    return recurrence_id, task, second


def test_skip_returns_live_successor_when_next_date_active(
    db_session: Session,
) -> None:
    # The next date already holds a live occurrence: return it, don't duplicate.
    recurrence_id, first, second = _two_live_occurrences(db_session)

    next_occurrence = task_recurrence.skip_occurrence(db_session, first)
    db_session.commit()

    assert next_occurrence.id == second.id
    assert next_occurrence.deleted_at is None
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 8)
    ]


def test_skip_into_normally_trashed_next_date_creates_live_occurrence(
    db_session: Session,
) -> None:
    # The next date holds only a normally-trashed row. Skip must still land a live,
    # actionable occurrence on that date rather than returning the dead row.
    recurrence_id, first, second = _two_live_occurrences(db_session)
    tasks_service.soft_delete_task(db_session, second)
    db_session.commit()
    assert second.deleted_at is not None and second.skipped_at is None

    next_occurrence = task_recurrence.skip_occurrence(db_session, first)
    db_session.commit()

    assert next_occurrence.due_date == date(2026, 6, 8)
    assert next_occurrence.deleted_at is None
    assert next_occurrence.id != second.id  # a fresh live row, not the trashed one
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 8)
    ]


def test_skip_past_skipped_next_date_returns_live_beyond(
    db_session: Session,
) -> None:
    # The next date was explicitly skipped earlier. Honor that: roll forward to the
    # live successor beyond it (spawned when it was skipped), never the skipped row.
    recurrence_id, first, second = _two_live_occurrences(db_session)
    third = task_recurrence.skip_occurrence(db_session, second)  # 06-08 skipped
    db_session.commit()
    assert second.skipped_at is not None
    assert third.due_date == date(2026, 6, 15) and third.deleted_at is None

    next_occurrence = task_recurrence.skip_occurrence(db_session, first)
    db_session.commit()

    assert next_occurrence.id == third.id
    assert next_occurrence.due_date == date(2026, 6, 15)
    assert next_occurrence.deleted_at is None
    # No live row revived on the explicitly-skipped 06-08.
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 15)
    ]


def test_restore_trashed_occurrence_after_skip_replaced_it_conflicts(
    db_session: Session,
) -> None:
    # Skipping onto a trashed date makes a live replacement there. Restoring the
    # trashed row would then put two live occurrences of one series on 06-08, so it
    # is refused: restore is never the write that breaks the uniqueness invariant.
    recurrence_id, first, second = _two_live_occurrences(db_session)
    tasks_service.soft_delete_task(db_session, second)
    db_session.commit()
    replacement = task_recurrence.skip_occurrence(db_session, first)
    db_session.commit()

    with pytest.raises(tasks_service.OccurrenceConflictError):
        task_trash.restore_task(db_session, second)
    db_session.rollback()

    live_on_0608 = [
        t
        for t in _series(db_session, recurrence_id)
        if t.due_date == date(2026, 6, 8) and t.deleted_at is None
    ]
    assert [t.id for t in live_on_0608] == [replacement.id]
    assert second.deleted_at is not None  # still in the trash, still restorable


def test_restore_conflict_resolves_after_trashing_the_replacement(
    db_session: Session,
) -> None:
    # The escape hatch the error message names: trash the live replacement, then the
    # restore goes through and the series is back to one live row on the date.
    recurrence_id, first, second = _two_live_occurrences(db_session)
    tasks_service.soft_delete_task(db_session, second)
    db_session.commit()
    replacement = task_recurrence.skip_occurrence(db_session, first)
    db_session.commit()
    tasks_service.soft_delete_task(db_session, replacement)
    db_session.commit()

    restored = task_trash.restore_task(db_session, second)
    db_session.commit()

    assert restored.id == second.id
    assert [t.id for t in _series(db_session, recurrence_id) if t.deleted_at is None] == [
        second.id
    ]


def test_restore_route_returns_409_on_occurrence_conflict(
    client: TestClient, db_session: Session
) -> None:
    _recurrence_id, first, second = _two_live_occurrences(db_session)
    tasks_service.soft_delete_task(db_session, second)
    db_session.commit()
    task_recurrence.skip_occurrence(db_session, first)
    db_session.commit()

    response = client.post(f"/api/tasks/{second.id}/restore")

    assert response.status_code == 409
    assert "2026-06-08" in response.json()["detail"]


# --- next_occurrence_date on the read payload (Slice 2) ---------------------


def test_next_occurrence_date_advances_open_recurring(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    assert _next_date(db_session, task) == date(2026, 6, 8)


def test_next_occurrence_date_none_without_interval(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))

    assert _next_date(db_session, task) is None


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

    assert _next_date(db_session, task) is None


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


def test_edit_scope_future_logs_event_per_occurrence(db_session: Session) -> None:
    # The forward-patch is a bulk UPDATE; every occurrence it mutates must still
    # land an activity event, like every other multi-row op. The acted-on row and
    # the forwarded future row each get an "updated" event.
    recurrence_id, series = _three_occurrence_series(db_session)
    first, second, third = series
    project_id = second.project_id
    assert project_id is not None

    events = activity_service.list_events(db_session, project_id, limit=200)
    last_event_id = events[0].id if events else 0

    tasks_service.update_task(
        db_session, second, {"title": "deep clean", "edit_scope": "future"}
    )
    db_session.commit()

    new_updated_ids = {
        e.entity_id
        for e in activity_service.list_events(db_session, project_id, limit=200)
        if e.id > last_event_id and e.action == "updated" and e.entity_type == "task"
    }
    # Both the acted-on row (06-08) and the forwarded future row (06-15) logged;
    # the untouched past row (06-01) did not.
    assert new_updated_ids == {second.id, third.id}
    assert first.id not in new_updated_ids


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


def test_clearing_due_date_on_recurring_task_is_rejected(db_session: Session) -> None:
    # Clearing the due date while the task stays recurring would leave a series
    # that never spawns and can't be skipped. The guard checks the post-patch view,
    # so an omitted repeat_interval doesn't sneak past it.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    with pytest.raises(tasks_service.RecurrenceError):
        tasks_service.update_task(db_session, task, {"due_date": None})
    db_session.rollback()

    # Left untouched: still recurring, still has its due date.
    assert task.due_date == date(2026, 6, 1)
    assert task.repeat_interval is not None


def test_clearing_due_date_and_repeat_together_succeeds(db_session: Session) -> None:
    # Clearing both at once is fine: with the recurrence gone there's no series to
    # leave stranded.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    tasks_service.update_task(
        db_session, task, {"due_date": None, "repeat_interval": None}
    )
    db_session.commit()

    assert task.due_date is None
    assert task.repeat_interval is None


def test_patch_clear_due_date_on_recurring_returns_422_over_http(
    client: TestClient, db_session: Session
) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    res = client.patch(f"/api/tasks/{task.id}", json={"due_date": None})

    assert res.status_code == 422


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


def test_stop_recurrence_from_historical_occurrence_stops_live_one(
    db_session: Session,
) -> None:
    # The timeline links past occurrences, so "Stop recurrence" is reachable from a
    # done row. Every row carries its own repeat_interval, so clearing only the row
    # acted on left the live occurrence spawning while the UI claimed the series
    # had stopped.
    recurrence_id, series = _three_occurrence_series(db_session)
    first, _second, third = series

    task_recurrence.stop_recurrence(db_session, first)
    db_session.commit()

    refreshed = _series(db_session, recurrence_id)
    assert all(t.repeat_interval is None for t in refreshed)
    assert all(t.recurrence_id == recurrence_id for t in refreshed)  # chain readable

    # The real regression: the live occurrence must not spawn a successor.
    before = _active_count(db_session)
    tasks_service.mark_done(db_session, third)
    db_session.commit()
    assert _active_count(db_session) == before


def test_stop_recurrence_from_live_occurrence_clears_past_rows_too(
    db_session: Session,
) -> None:
    # Guards against a fix that only walks forward from the acted-on row.
    recurrence_id, series = _three_occurrence_series(db_session)
    third = series[2]

    task_recurrence.stop_recurrence(db_session, third)
    db_session.commit()

    assert all(t.repeat_interval is None for t in _series(db_session, recurrence_id))


def test_stop_recurrence_clears_trashed_occurrence_too(db_session: Session) -> None:
    # A trashed occurrence keeps its repeat_interval; if a stop skipped it,
    # restoring it later would quietly resume the series the user stopped.
    recurrence_id, series = _three_occurrence_series(db_session)
    first, _second, third = series
    tasks_service.soft_delete_task(db_session, first)
    db_session.commit()

    task_recurrence.stop_recurrence(db_session, third)
    db_session.commit()

    all_rows = (
        db_session.execute(select(Task).where(Task.recurrence_id == recurrence_id))
        .scalars()
        .all()
    )
    assert len(all_rows) == 3  # the trashed row is in scope, not filtered out
    assert all(t.repeat_interval is None for t in all_rows)


def test_stop_recurrence_twice_raises(db_session: Session) -> None:
    _recurrence_id, series = _three_occurrence_series(db_session)
    first, _second, third = series

    task_recurrence.stop_recurrence(db_session, first)
    db_session.commit()

    # Nothing left to stop anywhere in the series.
    with pytest.raises(tasks_service.RecurrenceError):
        task_recurrence.stop_recurrence(db_session, third)


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


def test_reopen_and_recomplete_child_does_not_duplicate_checklist_occurrence(
    db_session: Session,
) -> None:
    # A checklist parent's status is derived, so reopening its last child re-derives
    # the roll-up to done and re-enters the spawn path via
    # maybe_spawn_recurring_checklist.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session)
    for child in children:
        tasks_service.mark_done(db_session, child)
    db_session.commit()
    assert len(_series(db_session, recurrence_id)) == 2

    tasks_service.reopen_task(db_session, children[-1])
    db_session.commit()
    tasks_service.mark_done(db_session, children[-1])
    db_session.commit()

    series = _series(db_session, recurrence_id)
    assert len(series) == 2
    assert series[0].id == parent.id
    # The duplicate would have brought a second cloned subtree with it.
    assert len(tasks_service.list_subtasks(db_session, series[-1].id)) == len(children)


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


def test_blocked_recurring_checklist_defers_spawn_until_blocker_done(
    db_session: Session,
) -> None:
    # A recurring checklist parent that itself depends on an unfinished blocker
    # must NOT spawn its next occurrence when its children finish; the spawn is
    # deferred until the blocker is completed.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session)
    blocker = tasks_service.create_task(
        db_session, project_id=parent.project_id, title="external blocker"
    )
    deps_service.add_dependency(db_session, parent.id, blocker.id)
    db_session.commit()

    for child in children:
        tasks_service.mark_done(db_session, child)
        db_session.commit()

    # Parent rolls up to done but is blocked: no next occurrence yet.
    assert len(_series(db_session, recurrence_id)) == 1

    # Completing the blocker rolls the series forward exactly once.
    tasks_service.mark_done(db_session, blocker)
    db_session.commit()
    series = _series(db_session, recurrence_id)
    assert len(series) == 2
    assert series[-1].due_date == date(2026, 6, 8)

    # Idempotent: re-completing the blocker (a no-op) spawns nothing further.
    tasks_service.mark_done(db_session, blocker)
    db_session.commit()
    assert len(_series(db_session, recurrence_id)) == 2


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


def test_stop_recurrence_from_historical_row_over_http(
    client: TestClient, db_session: Session
) -> None:
    recurrence_id, series = _three_occurrence_series(db_session)
    first, _second, third = series

    res = client.post(f"/api/tasks/{first.id}/stop-recurrence")
    assert res.status_code == 200
    assert res.json()["repeat_interval"] is None

    # The response is about the acted-on row; the point is the live one is stopped.
    live = client.get(f"/api/tasks/{third.id}")
    assert live.json()["repeat_interval"] is None
    assert live.json()["recurrence_id"] == recurrence_id


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


# --- Delete-vs-skip intent (BUG-04) -----------------------------------------
#
# A skipped occurrence and a normally-trashed one both carry ``deleted_at``; only
# ``skipped_at`` tells them apart. Without it, restore treated every recurring
# restore as an un-skip — dragging the live occurrence backward and purging the
# row the user asked to restore.


def _weekly_leaf_with_successor(db: Session) -> tuple[Task, Task, str]:
    """A completed weekly occurrence (06-01) and the successor it spawned (06-08)."""
    task = _make_task(db, due=date(2026, 6, 1))
    tasks_service.update_task(
        db, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None

    tasks_service.mark_done(db, task)
    db.commit()
    successor = _series(db, recurrence_id)[-1]
    assert successor.due_date == date(2026, 6, 8)
    return task, successor, recurrence_id


def test_restore_normally_deleted_occurrence_restores_in_place(
    db_session: Session,
) -> None:
    # The BUG-04 repro: trashing a completed past occurrence and restoring it must
    # bring back that row, not rewind the series onto its date and purge it.
    task, successor, recurrence_id = _weekly_leaf_with_successor(db_session)
    task_id = task.id

    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()
    restored = task_trash.restore_task(db_session, task)
    db_session.commit()

    # The restored row is the one that was asked for, at its own date, intact.
    assert restored.id == task_id
    assert restored.deleted_at is None
    assert restored.due_date == date(2026, 6, 1)
    # The live successor was not dragged backward, and nothing was purged.
    db_session.refresh(successor)
    assert successor.due_date == date(2026, 6, 8)
    assert successor.deleted_at is None
    assert {t.id for t in _series(db_session, recurrence_id)} == {task_id, successor.id}


def test_restore_normally_deleted_checklist_leaves_clones_alone(
    db_session: Session,
) -> None:
    # Same as above for a checklist occurrence: the successor's cloned subtree must
    # not be rescheduled backward with its parent.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session)
    for child in children:
        tasks_service.mark_done(db_session, child)
        db_session.commit()
    successor = _series(db_session, recurrence_id)[-1]
    assert successor.due_date == date(2026, 6, 8)

    tasks_service.soft_delete_task(db_session, parent)
    db_session.commit()
    restored = task_trash.restore_task(db_session, parent)
    db_session.commit()

    assert restored.id == parent.id
    assert restored.due_date == date(2026, 6, 1)
    db_session.refresh(successor)
    assert successor.due_date == date(2026, 6, 8)
    clones = tasks_service.list_subtasks(db_session, successor.id)
    assert {c.due_date for c in clones} == {date(2026, 6, 8)}


def test_skip_marks_skipped_at_and_delete_does_not(db_session: Session) -> None:
    task, successor, _recurrence_id = _weekly_leaf_with_successor(db_session)

    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()
    assert task.deleted_at is not None
    assert task.skipped_at is None  # ordinary delete records no intent

    task_recurrence.skip_occurrence(db_session, successor)
    db_session.commit()
    assert successor.deleted_at is not None
    assert successor.skipped_at == successor.deleted_at


def test_restore_skipped_occurrence_still_unskips(db_session: Session) -> None:
    # The existing un-skip behavior must not regress: it's now gated on skipped_at.
    task, successor, recurrence_id = _weekly_leaf_with_successor(db_session)
    successor_id = successor.id

    third = task_recurrence.skip_occurrence(db_session, successor)
    db_session.commit()
    assert third.due_date == date(2026, 6, 15)

    restored = task_trash.restore_task(db_session, successor)
    db_session.commit()

    # The series rewinds to the un-skipped date with exactly one live occurrence.
    assert restored.id == third.id
    assert restored.due_date == date(2026, 6, 8)
    assert successor_id not in {t.id for t in _series(db_session, recurrence_id)}
    assert task.id in {t.id for t in _series(db_session, recurrence_id)}


def test_restore_skipped_occurrence_without_successor_clears_skipped_at(
    db_session: Session,
) -> None:
    # No live sibling to rewind onto, so the skipped row restores in place — and
    # must not come back still flagged as skipped while it's active.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    successor = task_recurrence.skip_occurrence(db_session, task)
    db_session.commit()
    tasks_service.soft_delete_task(db_session, successor)
    db_session.commit()

    restored = task_trash.restore_task(db_session, task)
    db_session.commit()

    assert restored.id == task.id
    assert restored.deleted_at is None
    assert restored.skipped_at is None


def test_series_hides_normally_deleted_but_shows_skipped(
    db_session: Session,
) -> None:
    task, successor, recurrence_id = _weekly_leaf_with_successor(db_session)
    third = task_recurrence.skip_occurrence(db_session, successor)
    db_session.commit()
    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    series = task_recurrence.get_series(db_session, recurrence_id)

    # Skipped (successor) stays on the timeline; normally-trashed (task) leaves it.
    assert {t.id for t in series} == {successor.id, third.id}


def test_series_shows_normally_deleted_occurrence_again_once_restored(
    db_session: Session,
) -> None:
    task, _successor, recurrence_id = _weekly_leaf_with_successor(db_session)
    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()
    assert task.id not in {t.id for t in task_recurrence.get_series(db_session, recurrence_id)}

    task_trash.restore_task(db_session, task)
    db_session.commit()

    assert task.id in {t.id for t in task_recurrence.get_series(db_session, recurrence_id)}


# --- Reconciliation: every transition into effective completion ---------------
#
# Recurrence used to hang off the stored open->done write, so a series stalled
# whenever a task became *effectively* complete some other way. These cover each
# door, and the active-series uniqueness that the fixes lean on.


def test_attaching_recurrence_to_a_done_task_spawns_successor(
    db_session: Session,
) -> None:
    # Door 1: the task is already done when the interval is attached. There may
    # never be another open->done transition, so waiting for one stalls forever.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.mark_done(db_session, task)
    db_session.commit()

    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    recurrence_id = task.recurrence_id
    assert recurrence_id is not None
    series = _series(db_session, recurrence_id)
    assert [t.due_date for t in series] == [date(2026, 6, 1), date(2026, 6, 8)]
    assert task.workflow_status == TaskWorkflowStatus.done
    assert _effective(db_session, task) == TaskWorkflowStatus.done
    # The done head advertises no "next": its successor is already a visible row.
    assert _next_date(db_session, task) is None
    assert series[-1].workflow_status == TaskWorkflowStatus.open


def test_attaching_recurrence_to_a_done_task_without_due_date_still_422s(
    db_session: Session,
) -> None:
    # The reconciliation hook doesn't loosen the precondition: no due date, no series.
    task = _make_task(db_session, due=None)
    tasks_service.mark_done(db_session, task)
    db_session.commit()

    with pytest.raises(tasks_service.RecurrenceError):
        tasks_service.update_task(
            db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
        )


def test_finished_recurring_checklist_advertises_no_next_date(
    db_session: Session,
) -> None:
    # Door 2's read-side half: a checklist parent's status is derived, so it stays
    # stored-open forever. Reading the stored value made it advertise 06-08 while
    # the 06-08 occurrence already existed — two rows claiming the same date.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=1)
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()

    assert parent.workflow_status == TaskWorkflowStatus.open  # stored, derived
    assert _effective(db_session, parent) == TaskWorkflowStatus.done
    assert _next_date(db_session, parent) is None
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
    ]


def test_task_read_hides_next_date_for_finished_checklist(
    db_session: Session,
) -> None:
    from app.api.task_reads import read_with_blocked, reads_with_blocked

    parent, children, _recurrence_id = _recurring_parent_with_children(db_session, n=1)
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()

    assert read_with_blocked(db_session, parent).next_occurrence_date is None
    # The list path resolves it the same way (no divergence between surfaces).
    assert reads_with_blocked(db_session, [parent])[0].next_occurrence_date is None


def test_removing_last_blocker_spawns_recurring_checklist(
    db_session: Session,
) -> None:
    # Door 3: the checklist finished while blocked, so it couldn't spawn. Completing
    # the blocker was already handled; *removing* the edge is the same transition
    # into effective completion and must roll the series forward too.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=1)
    blocker = tasks_service.create_task(
        db_session, project_id=parent.project_id, title="blocker"
    )
    db_session.commit()
    edge = deps_service.add_dependency(
        db_session, task_id=parent.id, depends_on_id=blocker.id
    )
    db_session.commit()
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()
    # Blocked: done roll-up is capped, and nothing spawned.
    assert _effective(db_session, parent) == TaskWorkflowStatus.in_progress
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [date(2026, 6, 1)]

    deps_service.remove_dependency(db_session, edge)
    db_session.commit()

    assert _effective(db_session, parent) == TaskWorkflowStatus.done
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
    ]


def test_removing_a_non_final_blocker_spawns_nothing(db_session: Session) -> None:
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=1)
    first = tasks_service.create_task(
        db_session, project_id=parent.project_id, title="b1"
    )
    second = tasks_service.create_task(
        db_session, project_id=parent.project_id, title="b2"
    )
    db_session.commit()
    edge = deps_service.add_dependency(
        db_session, task_id=parent.id, depends_on_id=first.id
    )
    deps_service.add_dependency(db_session, task_id=parent.id, depends_on_id=second.id)
    db_session.commit()
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()

    deps_service.remove_dependency(db_session, edge)
    db_session.commit()

    assert _effective(db_session, parent) == TaskWorkflowStatus.in_progress
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [date(2026, 6, 1)]


def test_trashing_last_blocker_spawns_recurring_checklist(
    db_session: Session,
) -> None:
    # Same door as removing the edge: the blocker leaving via the trash unblocks the
    # checklist just as effectively.
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

    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
    ]


def test_trashing_last_open_child_spawns_recurring_checklist(
    db_session: Session,
) -> None:
    # Removing the remaining work is a completion too: one child done, the other
    # trashed leaves a subtree that rolls up done.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=2)
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()

    tasks_service.soft_delete_task(db_session, children[1])
    db_session.commit()

    assert _effective(db_session, parent) == TaskWorkflowStatus.done
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
    ]


def test_recompleting_after_trashing_successor_creates_replacement(
    db_session: Session,
) -> None:
    # A normally-trashed occurrence must not wedge its date: "delete this" is not
    # "I decided not to do this", so the slot is vacant and a re-completion refills
    # it. The trashed row stays in the trash.
    task, successor, recurrence_id = _weekly_leaf_with_successor(db_session)
    tasks_service.soft_delete_task(db_session, successor)
    db_session.commit()

    tasks_service.reopen_task(db_session, task)
    db_session.commit()
    tasks_service.mark_done(db_session, task)
    db_session.commit()

    live = _live_series(db_session, recurrence_id)
    assert [t.due_date for t in live] == [date(2026, 6, 1), date(2026, 6, 8)]
    replacement = live[-1]
    assert replacement.id != successor.id
    assert successor.deleted_at is not None and successor.skipped_at is None


def test_recompleting_after_skipping_successor_does_not_revive_the_date(
    db_session: Session,
) -> None:
    # The other half of the same rule: a *skipped* date stays skipped. The user said
    # that occurrence didn't happen; re-completing must not re-add it.
    task, successor, recurrence_id = _weekly_leaf_with_successor(db_session)
    task_recurrence.skip_occurrence(db_session, successor)  # spawns 06-15
    db_session.commit()

    tasks_service.reopen_task(db_session, task)
    db_session.commit()
    tasks_service.mark_done(db_session, task)
    db_session.commit()

    assert [t.due_date for t in _live_series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 15),
    ]


def test_reconcile_is_idempotent(db_session: Session) -> None:
    # Reconciliation runs after every mutation, so re-running it must never add a row.
    parent, children, recurrence_id = _recurring_parent_with_children(db_session, n=1)
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()
    before = [t.id for t in _series(db_session, recurrence_id)]

    task_recurrence.reconcile(db_session, [children[0].id, parent.id])
    task_recurrence.reconcile(db_session, [parent.id])
    db_session.commit()

    assert [t.id for t in _series(db_session, recurrence_id)] == before


def test_reconcile_spawns_at_most_once_across_dependency_diamond(
    db_session: Session,
) -> None:
    # A recurring checklist reachable from a completed blocker by two paths spawns
    # once: reconcile walks dependents transitively, and the diamond re-reaches the
    # head from both arms.
    head, children, recurrence_id = _recurring_parent_with_children(db_session, n=1)
    left = tasks_service.create_task(db_session, project_id=None, title="left")
    right = tasks_service.create_task(db_session, project_id=None, title="right")
    root = tasks_service.create_task(db_session, project_id=None, title="root")
    db_session.commit()
    for blocker in (left, right):
        deps_service.add_dependency(
            db_session, task_id=head.id, depends_on_id=blocker.id
        )
        deps_service.add_dependency(
            db_session, task_id=blocker.id, depends_on_id=root.id
        )
    db_session.commit()
    tasks_service.mark_done(db_session, children[0])
    db_session.commit()
    # Effectively done but blocked by both arms: the series is still one row.
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [date(2026, 6, 1)]

    tasks_service.mark_done(db_session, root)
    db_session.commit()
    tasks_service.mark_done(db_session, left)
    db_session.commit()
    # Completing the second arm unblocks the head, reached via left and via right.
    tasks_service.mark_done(db_session, right)
    db_session.commit()

    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
    ]


def test_active_occurrence_uniqueness_is_enforced_by_the_database(
    db_session: Session,
) -> None:
    # The service guards this, but the invariant is in the schema too: the paths that
    # can breach it are exactly the ones an app-level check gets wrong.
    from sqlalchemy.exc import IntegrityError

    task, _successor, recurrence_id = _weekly_leaf_with_successor(db_session)
    db_session.add(
        Task(
            project_id=task.project_id,
            title="twin",
            recurrence_id=recurrence_id,
            due_date=date(2026, 6, 8),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_soft_deleted_rows_are_exempt_from_uniqueness(db_session: Session) -> None:
    # Trash history is the whole point of soft deletes: many trashed rows may share
    # a date, which is why the index is partial.
    task, successor, recurrence_id = _weekly_leaf_with_successor(db_session)
    tasks_service.soft_delete_task(db_session, successor)
    db_session.commit()
    tasks_service.reopen_task(db_session, task)
    db_session.commit()
    replacement = tasks_service.mark_done(db_session, task) and _live_series(
        db_session, recurrence_id
    )[-1]
    tasks_service.soft_delete_task(db_session, replacement)
    db_session.commit()

    trashed_on_0608 = [
        t
        for t in db_session.execute(
            select(Task).where(
                Task.recurrence_id == recurrence_id,
                Task.due_date == date(2026, 6, 8),
                Task.deleted_at.is_not(None),
            )
        )
        .scalars()
        .all()
    ]
    assert len(trashed_on_0608) == 2


def test_trashing_recurring_parent_does_not_spawn_successor(
    db_session: Session,
) -> None:
    # BUG-96: a stored-done parent reopened by a later child keeps its stored
    # status done. The cascade deleted that child first and reconciled the
    # still-active parent, which then rolled up done and spawned an occurrence —
    # mid-delete. Deleting a subtree must never advance the series; skipping and
    # completing stay the explicit ways to do that.
    task = _make_task(db_session, due=date(2026, 7, 25))
    tasks_service.mark_done(db_session, task)
    db_session.commit()
    child = tasks_service.create_task(
        db_session,
        project_id=task.project_id,
        title="new work",
        parent_task_id=task.id,
    )
    db_session.commit()
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None
    # The open child holds the series: no successor yet.
    assert [t.due_date for t in _series(db_session, recurrence_id)] == [
        date(2026, 7, 25)
    ]

    tasks_service.soft_delete_task(db_session, task)
    db_session.commit()

    assert task.deleted_at is not None
    db_session.refresh(child)
    assert child.deleted_at is not None
    # Nothing live is left in the series — in particular no 2026-08-01 occurrence.
    assert list(_series(db_session, recurrence_id)) == []
