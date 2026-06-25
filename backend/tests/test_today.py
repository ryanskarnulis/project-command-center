from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models import TaskPriority, TaskWorkflowStatus
from app.schemas.today import DueSignal
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service
from app.services import today as today_service

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

    plan = today_service.get_today_plan(db_session, target_date=TARGET)

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

    plan = today_service.get_today_plan(db_session, target_date=TARGET)

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

    plan = today_service.get_today_plan(db_session, target_date=TARGET)

    assert [b.task_id for b in plan.scheduled] == [in_progress, open_task]


def test_priority_orders_after_due_signal(db_session: Session) -> None:
    low = _task(db_session, "low", priority=TaskPriority.low)
    urgent = _task(db_session, "urgent", priority=TaskPriority.urgent)
    high = _task(db_session, "high", priority=TaskPriority.high)

    plan = today_service.get_today_plan(db_session, target_date=TARGET)

    assert [b.task_id for b in plan.scheduled] == [urgent, high, low]


def test_blocked_task_excluded_and_surfaced(db_session: Session) -> None:
    blocker = _task(db_session, "blocker")
    dependent = _task(db_session, "dependent")
    deps_service.add_dependency(db_session, dependent, blocker)
    db_session.commit()

    plan = today_service.get_today_plan(db_session, target_date=TARGET)

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

    plan = today_service.get_today_plan(db_session, target_date=TARGET)

    assert plan.blocked == []
    assert dependent in [b.task_id for b in plan.scheduled]


def test_unsized_task_gets_assumed_estimate(db_session: Session) -> None:
    sized = _task(db_session, "sized", estimated_minutes=45)
    unsized = _task(db_session, "unsized")

    plan = today_service.get_today_plan(db_session, target_date=TARGET)
    by_id = {b.task_id: b for b in plan.scheduled}

    assert by_id[sized].estimated_minutes == 45
    assert by_id[sized].estimate_assumed is False
    assert by_id[unsized].estimated_minutes == today_service.DEFAULT_ESTIMATE_MINUTES
    assert by_id[unsized].estimate_assumed is True


def test_overflow_in_ranked_order_when_capacity_full(db_session: Session) -> None:
    first = _task(db_session, "first", priority=TaskPriority.urgent, estimated_minutes=60)
    second = _task(db_session, "second", priority=TaskPriority.high, estimated_minutes=60)
    third = _task(db_session, "third", priority=TaskPriority.low, estimated_minutes=60)

    plan = today_service.get_today_plan(
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

    plan = today_service.get_today_plan(
        db_session, target_date=TARGET, available_minutes=360
    )

    assert [b.task_id for b in plan.scheduled] == [small]
    assert [o.task_id for o in plan.overflow] == [oversized]
    assert plan.used_minutes <= plan.available_minutes


def test_block_times_are_sequential_from_start(db_session: Session) -> None:
    _task(db_session, "a", priority=TaskPriority.urgent, estimated_minutes=30)
    _task(db_session, "b", priority=TaskPriority.high, estimated_minutes=45)

    plan = today_service.get_today_plan(
        db_session, target_date=TARGET, start_time="09:00"
    )

    assert plan.scheduled[0].start_time == "09:00"
    assert plan.scheduled[0].end_time == "09:30"
    assert plan.scheduled[1].start_time == "09:30"
    assert plan.scheduled[1].end_time == "10:15"
    assert plan.used_minutes == 75
