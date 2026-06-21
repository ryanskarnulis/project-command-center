from collections.abc import Sequence
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskReviewStatus, TaskWorkflowStatus
from app.schemas.tasks import RepeatInterval
from app.services import projects as projects_service
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
    assert occurrence.review_status == TaskReviewStatus.accepted
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

    next_occurrence = tasks_service.skip_occurrence(db_session, task)
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


def test_skip_non_recurring_task_raises_422(db_session: Session) -> None:
    task = _make_task(db_session, due=date(2026, 6, 1))

    with pytest.raises(HTTPException) as exc:
        tasks_service.skip_occurrence(db_session, task)
    assert exc.value.status_code == 422


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


def test_setting_repeat_without_due_date_raises_422(db_session: Session) -> None:
    task = _make_task(db_session, due=None)

    with pytest.raises(HTTPException) as exc:
        tasks_service.update_task(
            db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
        )
    assert exc.value.status_code == 422


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
