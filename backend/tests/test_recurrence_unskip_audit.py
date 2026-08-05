"""Un-skipping a recurring checklist audits every due date it rewinds — issue #243.

``task_trash.restore_task``'s un-skip rewind calls
``task_recurrence.reschedule_occurrence``, which walks the live successor's whole
active subtree and resets each row's ``due_date``. That pass bypasses
``update_task``, so every cloned subtask's user-visible date used to change with
no activity event and no actor — total attribution loss for an agent-driven
un-skip. Only the occurrence root was logged, and only as ``restored``.

Now each row the rewind *actually changes* gets its own ``updated`` event, the
root keeps its single ``restored`` event, and a descendant already sitting on the
target date gains nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActivityEvent, Task
from app.services import projects as projects_service
from app.services import task_recurrence, task_trash
from app.services import tasks as tasks_service
from app.tools import registry, runtime

SKIPPED_DUE = date(2026, 8, 5)
SUCCESSOR_DUE = date(2026, 8, 12)


def _events(db: Session, task_id: int) -> Sequence[ActivityEvent]:
    return (
        db.execute(
            select(ActivityEvent)
            .where(
                ActivityEvent.entity_type == "task",
                ActivityEvent.entity_id == task_id,
            )
            .order_by(ActivityEvent.id.asc())
        )
        .scalars()
        .all()
    )


def _actions(db: Session, task_id: int) -> list[str]:
    return [event.action for event in _events(db, task_id)]


def _child_of(db: Session, parent_id: int) -> Task:
    children = (
        db.execute(
            select(Task)
            .where(Task.parent_task_id == parent_id, Task.deleted_at.is_(None))
            .order_by(Task.id.asc())
        )
        .scalars()
        .all()
    )
    assert len(children) == 1
    return children[0]


def _skipped_nested_checklist(db: Session) -> tuple[int, int, int, int]:
    """A skipped weekly checklist two levels deep, with its live successor cloned.

    Returns ``(skipped_root_id, live_id, live_child_id, live_grandchild_id)``.
    """
    project = projects_service.create_project(db, name="Weekly")
    parent = tasks_service.create_task(
        db, project_id=project.id, title="weekly checklist", due_date=SKIPPED_DUE
    )
    db.commit()
    tasks_service.update_task(
        db, parent, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db.commit()
    child = tasks_service.create_task(
        db, project_id=project.id, parent_task_id=parent.id, title="step one"
    )
    db.commit()
    tasks_service.create_task(
        db, project_id=project.id, parent_task_id=child.id, title="sub step"
    )
    db.commit()
    skipped_root_id = parent.id

    live = task_recurrence.skip_occurrence(db, parent)
    db.commit()
    live_child = _child_of(db, live.id)
    live_grandchild = _child_of(db, live_child.id)
    assert live.due_date == SUCCESSOR_DUE
    assert live_child.due_date == SUCCESSOR_DUE
    assert live_grandchild.due_date == SUCCESSOR_DUE
    return skipped_root_id, live.id, live_child.id, live_grandchild.id


def test_unskip_rewinds_and_audits_every_changed_descendant(
    db_session: Session,
) -> None:
    skipped_id, live_id, child_id, grandchild_id = _skipped_nested_checklist(
        db_session
    )
    assert _actions(db_session, child_id) == ["created"]
    assert _actions(db_session, grandchild_id) == ["created"]

    skipped = task_trash.get_deleted_task(db_session, skipped_id)
    assert skipped is not None
    live = task_trash.restore_task(db_session, skipped)
    db_session.commit()

    # The whole checklist is rewound, grandchild included.
    assert live.id == live_id
    assert live.due_date == SKIPPED_DUE
    assert db_session.get(Task, child_id).due_date == SKIPPED_DUE  # type: ignore[union-attr]
    assert db_session.get(Task, grandchild_id).due_date == SKIPPED_DUE  # type: ignore[union-attr]

    # ...and every rewritten row says so, nested descendants included.
    assert _actions(db_session, child_id) == ["created", "updated"]
    assert _actions(db_session, grandchild_id) == ["created", "updated"]

    # The root keeps its one meaningful event; it is not also logged as updated.
    assert _actions(db_session, live_id) == ["created", "restored"]


def test_unskip_events_carry_the_current_actor(db_session: Session) -> None:
    """Attribution is the point: the rewind is somebody's write, not the system's."""
    skipped_id, _live_id, child_id, grandchild_id = _skipped_nested_checklist(
        db_session
    )
    skipped = task_trash.get_deleted_task(db_session, skipped_id)
    assert skipped is not None
    task_trash.restore_task(db_session, skipped)
    db_session.commit()

    for task_id in (child_id, grandchild_id):
        rewind_event = _events(db_session, task_id)[-1]
        assert rewind_event.action == "updated"
        # No actor bound: the UI/API default, exactly what a user-driven restore
        # records elsewhere. The agent path is covered below.
        assert rewind_event.actor is None


def test_unskip_does_not_log_a_descendant_already_on_the_target_date(
    db_session: Session,
) -> None:
    """No-op rows must not gain a false ``updated`` — and the walk must not stop."""
    skipped_id, _live_id, child_id, grandchild_id = _skipped_nested_checklist(
        db_session
    )
    # Hand-move the middle row onto the un-skip target ahead of time. Its child
    # stays on the successor's date, so the rewind still has work below it.
    child = db_session.get(Task, child_id)
    assert child is not None
    tasks_service.update_task(db_session, child, {"due_date": SKIPPED_DUE})
    db_session.commit()
    assert db_session.get(Task, grandchild_id).due_date == SUCCESSOR_DUE  # type: ignore[union-attr]
    child_events_before = len(_events(db_session, child_id))

    skipped = task_trash.get_deleted_task(db_session, skipped_id)
    assert skipped is not None
    task_trash.restore_task(db_session, skipped)
    db_session.commit()

    # Unchanged row: no new event at all.
    assert len(_events(db_session, child_id)) == child_events_before
    # Changed row *below* it: still rewound and still audited.
    assert db_session.get(Task, grandchild_id).due_date == SKIPPED_DUE  # type: ignore[union-attr]
    assert _actions(db_session, grandchild_id) == ["created", "updated"]


def test_tool_unskip_attributes_the_rewind_to_the_agent(
    db_session: Session,
    test_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API/tool path: an agent-driven un-skip is attributable per changed row."""
    skipped_id, live_id, child_id, grandchild_id = _skipped_nested_checklist(
        db_session
    )
    # The in-memory engine hands out one connection (StaticPool); release the
    # test session's transaction so the tool's own session can open one.
    db_session.close()
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)

    result = registry.call_tool(
        "restore_task", {"task_id": skipped_id}, actor="agent:mcp"
    )

    assert result.id == live_id
    assert result.due_date == SKIPPED_DUE
    for task_id in (child_id, grandchild_id):
        rewind_event = _events(db_session, task_id)[-1]
        assert rewind_event.action == "updated"
        assert rewind_event.actor == "agent:mcp"
    assert _actions(db_session, live_id) == ["created", "restored"]
