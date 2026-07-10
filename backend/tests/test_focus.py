from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models import TaskPriority, TaskWorkflowStatus
from app.schemas.focus import DueSignal
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service
from app.services import focus as focus_service

TARGET = date(2026, 6, 20)


def _task(
    db: Session,
    title: str,
    *,
    priority: TaskPriority = TaskPriority.medium,
    workflow_status: TaskWorkflowStatus = TaskWorkflowStatus.open,
    due_date: date | None = None,
    estimated_minutes: int | None = None,
) -> int:
    task = tasks_service.create_task(
        db,
        project_id=None,
        title=title,
        priority=priority,
        workflow_status=workflow_status,
        due_date=due_date,
        estimated_minutes=estimated_minutes,
    )
    db.commit()
    return task.id


def test_due_urgency_orders_overdue_first(db_session: Session) -> None:
    no_due = _task(db_session, "no due")
    soon = _task(db_session, "soon", due_date=TARGET + timedelta(days=2))
    today = _task(db_session, "today", due_date=TARGET)
    overdue = _task(db_session, "overdue", due_date=TARGET - timedelta(days=1))

    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)

    assert [b.task_id for b in plan.scheduled] == [overdue, today, soon, no_due]
    assert [b.due_signal for b in plan.scheduled] == [
        DueSignal.overdue,
        DueSignal.due_today,
        DueSignal.due_soon,
        DueSignal.none,
    ]


def test_subtasks_are_excluded_from_the_plan(db_session: Session) -> None:
    parent = _task(db_session, "parent")
    subtask = tasks_service.create_task(
        db_session,
        project_id=None,
        title="subtask",
        parent_task_id=parent,
    )
    db_session.commit()

    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)

    scheduled_ids = [b.task_id for b in plan.scheduled]
    assert parent in scheduled_ids
    assert subtask.id not in scheduled_ids
    assert subtask.id not in [o.task_id for o in plan.overflow]


def test_in_progress_outranks_open_at_equal_due_and_priority(
    db_session: Session,
) -> None:
    open_task = _task(db_session, "open")
    in_progress = _task(
        db_session, "wip", workflow_status=TaskWorkflowStatus.in_progress
    )

    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)

    assert [b.task_id for b in plan.scheduled] == [in_progress, open_task]


def test_priority_orders_after_due_signal(db_session: Session) -> None:
    low = _task(db_session, "low", priority=TaskPriority.low)
    urgent = _task(db_session, "urgent", priority=TaskPriority.urgent)
    high = _task(db_session, "high", priority=TaskPriority.high)

    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)

    assert [b.task_id for b in plan.scheduled] == [urgent, high, low]


def test_blocked_task_excluded_and_surfaced(db_session: Session) -> None:
    blocker = _task(db_session, "blocker")
    dependent = _task(db_session, "dependent")
    deps_service.add_dependency(db_session, dependent, blocker)
    db_session.commit()

    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)

    scheduled_ids = [b.task_id for b in plan.scheduled]
    assert dependent not in scheduled_ids
    assert blocker in scheduled_ids
    assert [b.task_id for b in plan.blocked] == [dependent]
    blocking = plan.blocked[0].blocking_tasks
    assert [b.task_id for b in blocking] == [blocker]
    # The blocker carries its title + workflow status so the row is self-explanatory.
    assert blocking[0].title == "blocker"
    assert blocking[0].workflow_status == TaskWorkflowStatus.open


def test_done_dependency_unblocks_task(db_session: Session) -> None:
    blocker = _task(db_session, "blocker")
    dependent = _task(db_session, "dependent")
    deps_service.add_dependency(db_session, dependent, blocker)
    db_session.commit()

    blocker_task = tasks_service.get_task(db_session, blocker)
    assert blocker_task is not None
    tasks_service.update_task(
        db_session, blocker_task, {"workflow_status": TaskWorkflowStatus.done}
    )
    db_session.commit()

    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)

    assert plan.blocked == []
    assert dependent in [b.task_id for b in plan.scheduled]


def test_unsized_task_gets_assumed_estimate(db_session: Session) -> None:
    sized = _task(db_session, "sized", estimated_minutes=45)
    unsized = _task(db_session, "unsized")

    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)
    by_id = {b.task_id: b for b in plan.scheduled}

    assert by_id[sized].estimated_minutes == 45
    assert by_id[sized].estimate_assumed is False
    assert by_id[unsized].estimated_minutes == focus_service.DEFAULT_ESTIMATE_MINUTES
    assert by_id[unsized].estimate_assumed is True


def test_overflow_in_ranked_order_when_capacity_full(db_session: Session) -> None:
    first = _task(db_session, "first", priority=TaskPriority.urgent, estimated_minutes=60)
    second = _task(db_session, "second", priority=TaskPriority.high, estimated_minutes=60)
    third = _task(db_session, "third", priority=TaskPriority.low, estimated_minutes=60)

    plan = focus_service.get_focus_plan(
        db_session, target_date=TARGET, available_minutes=90
    )

    assert [b.task_id for b in plan.scheduled] == [first]
    assert [o.task_id for o in plan.overflow] == [second, third]
    assert plan.used_minutes <= plan.available_minutes


def test_backfill_schedules_smaller_task_when_top_task_overflows(
    db_session: Session,
) -> None:
    # An oversized top-ranked task must not strand the rest of the day: smaller
    # lower-ranked tasks still backfill the remaining capacity.
    oversized = _task(
        db_session, "oversized", priority=TaskPriority.urgent, estimated_minutes=720
    )
    small = _task(
        db_session, "small", priority=TaskPriority.low, estimated_minutes=30
    )

    plan = focus_service.get_focus_plan(
        db_session, target_date=TARGET, available_minutes=360
    )

    assert [b.task_id for b in plan.scheduled] == [small]
    assert [o.task_id for o in plan.overflow] == [oversized]
    assert plan.used_minutes <= plan.available_minutes


def test_oversized_parent_falls_back_to_fitting_subtasks(
    db_session: Session,
) -> None:
    # A parent too large for the remaining capacity gets its open subtasks tried
    # in its rank slot; those that fit are scheduled with the parent label, and
    # the parent overflows with the count.
    parent = _task(
        db_session, "big parent", priority=TaskPriority.urgent, estimated_minutes=720
    )
    fits = tasks_service.create_task(
        db_session, project_id=None, title="fits", parent_task_id=parent,
        estimated_minutes=60,
    )
    too_big = tasks_service.create_task(
        db_session, project_id=None, title="too big", parent_task_id=parent,
        estimated_minutes=600,
    )
    done = tasks_service.create_task(
        db_session, project_id=None, title="done", parent_task_id=parent,
        workflow_status=TaskWorkflowStatus.done, estimated_minutes=15,
    )
    db_session.commit()

    plan = focus_service.get_focus_plan(
        db_session, target_date=TARGET, available_minutes=120
    )

    scheduled_ids = [b.task_id for b in plan.scheduled]
    assert fits.id in scheduled_ids
    assert too_big.id not in scheduled_ids
    assert done.id not in scheduled_ids
    block = next(b for b in plan.scheduled if b.task_id == fits.id)
    assert block.parent_task_id == parent
    assert block.parent_title == "big parent"
    assert block.reason.startswith("part of big parent")
    overflow = next(o for o in plan.overflow if o.task_id == parent)
    assert overflow.scheduled_subtask_count == 1
    assert plan.used_minutes <= plan.available_minutes


def test_deferred_task_excluded_until_deferral_passes(db_session: Session) -> None:
    deferred = _task(db_session, "deferred")
    kept = _task(db_session, "kept")
    deferred_task = tasks_service.get_task(db_session, deferred)
    assert deferred_task is not None
    tasks_service.update_task(
        db_session, deferred_task, {"deferred_until": TARGET + timedelta(days=1)}
    )
    db_session.commit()

    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)
    assert [b.task_id for b in plan.scheduled] == [kept]
    assert deferred not in [o.task_id for o in plan.overflow]

    # Once the target date reaches deferred_until, the task is back in the plan.
    later = focus_service.get_focus_plan(
        db_session, target_date=TARGET + timedelta(days=1)
    )
    assert deferred in [b.task_id for b in later.scheduled]


def test_block_times_are_sequential_from_start(db_session: Session) -> None:
    _task(db_session, "a", priority=TaskPriority.urgent, estimated_minutes=30)
    _task(db_session, "b", priority=TaskPriority.high, estimated_minutes=45)

    plan = focus_service.get_focus_plan(
        db_session, target_date=TARGET, start_time="09:00"
    )

    assert plan.scheduled[0].start_time == "09:00"
    assert plan.scheduled[0].end_time == "09:30"
    assert plan.scheduled[1].start_time == "09:30"
    assert plan.scheduled[1].end_time == "10:15"
    assert plan.used_minutes == 75


def test_scheduled_and_overflow_flag_recurring(db_session: Session) -> None:
    plain = _task(db_session, "plain", due_date=TARGET, estimated_minutes=30)
    rec = _task(db_session, "rec", due_date=TARGET, estimated_minutes=30)
    task = tasks_service.get_task(db_session, rec)
    assert task is not None
    tasks_service.update_task(
        db_session, task, {"repeat_interval": {"unit": "week", "every": 1}}
    )
    db_session.commit()

    # Scheduled: the recurring block is flagged, the plain one isn't.
    plan = focus_service.get_focus_plan(db_session, target_date=TARGET)
    scheduled = {b.task_id: b.is_recurring for b in plan.scheduled}
    assert scheduled[rec] is True
    assert scheduled[plain] is False

    # Overflow carries the flag too (tiny capacity forces both to overflow).
    tight = focus_service.get_focus_plan(
        db_session, target_date=TARGET, available_minutes=15
    )
    overflow = {o.task_id: o.is_recurring for o in tight.overflow}
    assert overflow[rec] is True
    assert overflow[plain] is False
