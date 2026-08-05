"""Recurrence at the top of the supported date range (issue #232).

``date`` stops at 9999-12-31. A task due on that date used to accept any positive
repeat interval: the PATCH committed the recurrence and *then* 500'd deriving
``next_occurrence_date`` for its response, leaving a row that no read path could
serialize and the UI could not repair. The rule now lives in the service layer —
a recurrence must have a representable next due date before it is persisted — and
the read path degrades to "no next occurrence" instead of raising.
"""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.task_reads import read_with_blocked
from app.db.models import Task, TaskWorkflowStatus
from app.services import projects as projects_service
from app.services import task_recurrence
from app.services import tasks as tasks_service

# One interval per unit that walks off the end of the calendar from ``due``, and
# the latest ``due`` from which the same interval still lands on a real date.
OVERFLOWING = [
    ({"unit": "day", "every": 1}, date(9999, 12, 31)),
    ({"unit": "week", "every": 1}, date(9999, 12, 31)),
    ({"unit": "month", "every": 1}, date(9999, 12, 31)),
    ({"unit": "month", "every": 12}, date(9999, 1, 31)),
]


def _make_task(db: Session, *, due: date) -> Task:
    project = projects_service.create_project(db, name="Edge of time")
    task = tasks_service.create_task(
        db, project_id=project.id, title="water plants", due_date=due
    )
    db.commit()
    return task


# --- The date math itself ---------------------------------------------------


@pytest.mark.parametrize(("interval", "due"), OVERFLOWING)
def test_next_due_date_is_none_past_the_maximum(
    interval: dict[str, Any], due: date
) -> None:
    # day/week overflow raises OverflowError inside timedelta arithmetic, month
    # raises ValueError from ``date(10000, ...)``. Neither escapes any more.
    assert task_recurrence.next_due_date(due, interval) is None


@pytest.mark.parametrize(
    ("interval", "due", "expected"),
    [
        ({"unit": "day", "every": 1}, date(9999, 12, 30), date(9999, 12, 31)),
        ({"unit": "week", "every": 1}, date(9999, 12, 24), date(9999, 12, 31)),
        ({"unit": "month", "every": 1}, date(9999, 11, 30), date(9999, 12, 30)),
    ],
)
def test_next_due_date_still_lands_on_the_last_representable_dates(
    interval: dict[str, Any], due: date, expected: date
) -> None:
    assert task_recurrence.next_due_date(due, interval) == expected


def test_next_due_date_still_raises_on_an_unknown_unit() -> None:
    # The overflow guard must not swallow a genuinely bad interval: an unknown
    # unit is a bug, not "this series has no next date".
    with pytest.raises(ValueError, match="Unknown recurrence unit"):
        task_recurrence.next_due_date(date(2026, 6, 1), {"unit": "year", "every": 1})


def test_require_next_due_date_reports_the_limit() -> None:
    with pytest.raises(tasks_service.RecurrenceError) as exc_info:
        task_recurrence.require_next_due_date(
            date(9999, 12, 31), {"unit": "day", "every": 1}
        )

    assert "9999-12-31" in str(exc_info.value)


# --- The write path rejects before persisting -------------------------------


@pytest.mark.parametrize(("interval", "due"), OVERFLOWING)
def test_update_rejects_recurrence_without_a_representable_next_date(
    db_session: Session, interval: dict[str, Any], due: date
) -> None:
    task = _make_task(db_session, due=due)

    with pytest.raises(tasks_service.RecurrenceError):
        tasks_service.update_task(db_session, task, {"repeat_interval": interval})

    db_session.rollback()
    assert task.repeat_interval is None
    assert task.recurrence_id is None


def test_update_rejects_moving_a_recurring_task_past_the_maximum(
    db_session: Session,
) -> None:
    # The other direction of the same rule: the interval is already stored and the
    # patch moves the due date, so the check runs against the post-patch view.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    with pytest.raises(tasks_service.RecurrenceError):
        tasks_service.update_task(db_session, task, {"due_date": date(9999, 12, 31)})

    db_session.rollback()
    assert task.due_date == date(2026, 6, 1)


def test_moving_the_due_date_earlier_still_allows_the_recurrence(
    db_session: Session,
) -> None:
    # The guard only rejects the unrepresentable case; a normal reschedule of a
    # recurring task is untouched.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "month", "every": 1}}
    )
    db_session.commit()

    tasks_service.update_task(db_session, task, {"due_date": date(2026, 7, 15)})
    db_session.commit()

    assert task_recurrence.next_occurrence_date(task, TaskWorkflowStatus.open) == date(
        2026, 8, 15
    )


def test_future_scope_rejects_a_cadence_a_later_occurrence_cannot_carry(
    db_session: Session,
) -> None:
    # "This and all future occurrences" forwards repeat_interval with a bulk
    # UPDATE, so a cadence that is fine here can still poison a later-dated
    # sibling. The series is stopped and one row pushed to the end of the calendar
    # first — the only way to build that shape now that every other path is
    # guarded.
    task = _make_task(db_session, due=date(2026, 6, 1))
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()
    tasks_service.mark_done(db_session, task)
    db_session.commit()
    recurrence_id = task.recurrence_id
    assert recurrence_id is not None
    successor = task_recurrence.get_series(db_session, recurrence_id)[-1]
    task_recurrence.stop_recurrence(db_session, task)
    tasks_service.update_task(db_session, successor, {"due_date": date(9999, 12, 31)})
    db_session.commit()

    # The message names the *sibling's* date: the acted-on row's own cadence is
    # representable, so this can only be the forward-scope guard.
    with pytest.raises(
        tasks_service.RecurrenceError, match="Repeating from 9999-12-31"
    ):
        tasks_service.update_task(
            db_session,
            task,
            {"repeat_interval": {"unit": "day", "every": 1}, "edit_scope": "future"},
        )

    db_session.rollback()
    assert successor.repeat_interval is None
    assert task.repeat_interval is None


# --- Over HTTP: rejected, and the task stays readable -----------------------


@pytest.mark.parametrize(("interval", "due"), OVERFLOWING)
def test_rejected_patch_leaves_the_task_non_recurring_and_readable(
    client: TestClient, interval: dict[str, Any], due: date
) -> None:
    created = client.post(
        "/api/tasks", json={"title": "max-date", "due_date": due.isoformat()}
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    patched = client.patch(f"/api/tasks/{task_id}", json={"repeat_interval": interval})
    assert patched.status_code == 422
    assert "9999-12-31" in patched.json()["detail"]

    # The write never landed, and the read path that used to 500 works.
    reread = client.get(f"/api/tasks/{task_id}")
    assert reread.status_code == 200
    assert reread.json()["repeat_interval"] is None
    assert reread.json()["next_occurrence_date"] is None


# --- A row poisoned before the guard existed --------------------------------


def _poisoned_task(db: Session) -> Task:
    """A recurring task whose next occurrence is past ``date.max``.

    Written straight onto the ORM object on purpose: the service layer now refuses
    to produce this state, and these tests are about the rows that predate it.
    """
    task = _make_task(db, due=date(9999, 12, 31))
    task.repeat_interval = {"unit": "day", "every": 1}
    task.recurrence_id = "legacy-series"
    db.commit()
    return task


def test_poisoned_row_reads_as_having_no_next_occurrence(db_session: Session) -> None:
    task = _poisoned_task(db_session)

    assert task_recurrence.next_occurrence_date(task, TaskWorkflowStatus.open) is None
    assert read_with_blocked(db_session, task).next_occurrence_date is None


def test_poisoned_row_is_still_fetchable_over_http(
    client: TestClient, db_session: Session
) -> None:
    task = _poisoned_task(db_session)

    response = client.get(f"/api/tasks/{task.id}")

    assert response.status_code == 200
    assert response.json()["next_occurrence_date"] is None
    assert response.json()["repeat_interval"] == {"unit": "day", "every": 1}


def test_completing_a_poisoned_row_is_a_422_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    task = _poisoned_task(db_session)

    response = client.post(f"/api/tasks/{task.id}/done")

    assert response.status_code == 422
    db_session.rollback()
    assert task.workflow_status is TaskWorkflowStatus.open
    assert task_recurrence.get_series(db_session, "legacy-series") == [task]


def test_poisoned_row_can_be_repaired_by_moving_its_due_date(
    client: TestClient, db_session: Session
) -> None:
    task = _poisoned_task(db_session)

    response = client.patch(
        f"/api/tasks/{task.id}", json={"due_date": "2026-06-01"}
    )

    assert response.status_code == 200
    assert response.json()["next_occurrence_date"] == "2026-06-02"
