"""``GET /api/agent/glance`` (#504): today's and overdue tasks, today's Focus.

The contract is ``../agent-standard/app-yaml-glance-block.md``: no arguments,
read-only, no model call, an entity-ref shape, about 150 tokens (~600 chars).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, Task, TaskWorkflowStatus
from app.services import focus_plan as focus_plan_service
from app.services import tasks as tasks_service

TODAY = date.today()
DAY = timedelta(days=1)
# ~150 tokens; the contract's own conversion is "roughly 600 characters of JSON".
MAX_CHARS = 600


def _task(db: Session, title: str, due: date | None = None, **kwargs: Any) -> Task:
    task = tasks_service.create_task(
        db, project_id=None, title=title, due_date=due, **kwargs
    )
    db.commit()
    return task


def _glance(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/agent/glance")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _rendered(body: dict[str, Any]) -> str:
    return json.dumps(body, separators=(",", ":"))


def test_empty_day(client: TestClient) -> None:
    body = _glance(client)

    assert set(body) == {"as_of", "due_today", "overdue", "focus", "next_due"}
    assert body["due_today"] == []
    assert body["overdue"] == 0
    assert body["focus"] is None
    assert body["next_due"] is None
    assert body["as_of"]


def test_due_today_are_entity_refs(client: TestClient, db_session: Session) -> None:
    task = _task(db_session, "Pay rent", TODAY)
    _task(db_session, "Not today", TODAY + DAY)
    _task(db_session, "No due date")

    body = _glance(client)

    assert body["due_today"] == [
        {"app": "tasks", "kind": "task", "id": str(task.id), "title": "Pay rent"}
    ]
    assert body["overdue"] == 0


def test_overdue_is_a_count_not_a_list(client: TestClient, db_session: Session) -> None:
    _task(db_session, "Late one", TODAY - DAY)
    _task(db_session, "Later one", TODAY - 5 * DAY)
    _task(db_session, "Today", TODAY)

    body = _glance(client)

    assert body["overdue"] == 2
    assert [t["title"] for t in body["due_today"]] == ["Today"]


def test_done_and_trashed_tasks_do_not_count(
    client: TestClient, db_session: Session
) -> None:
    done = _task(db_session, "Finished", TODAY - DAY)
    tasks_service.update_task(
        db_session, done, {"workflow_status": TaskWorkflowStatus.done}
    )
    gone = _task(db_session, "Trashed", TODAY)
    gone.deleted_at = func.now()
    db_session.commit()

    body = _glance(client)

    assert body["overdue"] == 0
    assert body["due_today"] == []


def test_next_due_is_the_earliest_open_due_date_from_today(
    client: TestClient, db_session: Session
) -> None:
    _task(db_session, "Past", TODAY - 3 * DAY)
    _task(db_session, "Far", TODAY + 9 * DAY)
    _task(db_session, "Soon", TODAY + 2 * DAY)

    assert _glance(client)["next_due"] == (TODAY + 2 * DAY).isoformat()


def test_focus_set_and_unset(client: TestClient, db_session: Session) -> None:
    first = _task(db_session, "Call Sam")
    second = _task(db_session, "Second pin")
    assert _glance(client)["focus"] is None

    focus_plan_service.pin_task(db_session, plan_date=TODAY, task=first)
    focus_plan_service.pin_task(db_session, plan_date=TODAY, task=second)
    db_session.commit()
    assert _glance(client)["focus"] == {
        "app": "tasks",
        "kind": "task",
        "id": str(first.id),
        "title": "Call Sam",
    }

    entry = focus_plan_service.list_entries(db_session, TODAY)[0]
    focus_plan_service.remove_entry(db_session, entry)
    db_session.commit()
    assert _glance(client)["focus"]["id"] == str(second.id)


def test_focus_ignores_other_days_and_finished_tasks(
    client: TestClient, db_session: Session
) -> None:
    tomorrow = _task(db_session, "Tomorrow's")
    focus_plan_service.pin_task(db_session, plan_date=TODAY + DAY, task=tomorrow)
    finished = _task(db_session, "Already done")
    focus_plan_service.pin_task(db_session, plan_date=TODAY, task=finished)
    tasks_service.update_task(
        db_session, finished, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()

    assert _glance(client)["focus"] is None


def test_lists_are_capped_with_a_count_for_the_rest(
    client: TestClient, db_session: Session
) -> None:
    for n in range(9):
        _task(db_session, f"Task {n}", TODAY)

    body = _glance(client)

    assert len(body["due_today"]) < 9
    assert body["due_today_more"] == 9 - len(body["due_today"])


def test_worst_case_stays_inside_the_token_budget(
    client: TestClient, db_session: Session
) -> None:
    long_title = "An extremely long task title " * 20
    for _ in range(30):
        _task(db_session, long_title, TODAY)
    _task(db_session, "Late", TODAY - DAY)
    focused = _task(db_session, long_title)
    focus_plan_service.pin_task(db_session, plan_date=TODAY, task=focused)
    db_session.commit()

    body = _glance(client)

    assert len(_rendered(body)) <= MAX_CHARS
    assert body["due_today"][0]["title"].endswith("…")
    assert body["focus"]["title"].endswith("…")


def test_read_only_no_audit_rows(client: TestClient, db_session: Session) -> None:
    _task(db_session, "Something", TODAY)
    before = db_session.execute(
        select(func.count()).select_from(ActivityEvent)
    ).scalar_one()

    _glance(client)
    _glance(client)

    after = db_session.execute(
        select(func.count()).select_from(ActivityEvent)
    ).scalar_one()
    assert after == before


def test_manifest_declares_the_glance() -> None:
    # No YAML dependency in the backend; the block is a few flat lines.
    manifest = (Path(__file__).resolve().parents[2] / "app.yaml").read_text()

    assert "\nglance:\n" in manifest
    assert "\n  path: /api/agent/glance\n" in manifest
    assert "\n  holds: " in manifest
    assert "\nagent:\n" in manifest
