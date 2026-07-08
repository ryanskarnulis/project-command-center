from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.gateway import GatewayError
from app.ai.workflows import break_down_task as breakdown_workflow
from app.api.guards import trashed_row_or_error
from app.api.rate_limit import rate_limit
from app.api.task_reads import read_with_blocked, reads_with_blocked
from app.db.models import Task, TaskReviewStatus, TaskWorkflowStatus
from app.db.session import get_db
from app.schemas.tasks import (
    BreakdownReviewRequest,
    BreakdownReviewResult,
    TaskCreate,
    TaskRead,
    TaskSeries,
    TaskUpdate,
)
from app.services import breakdown as breakdown_service
from app.services import projects as projects_service
from app.services import task_recurrence, task_trash
from app.services import tasks as tasks_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["tasks"])


def _get_task_or_404(db: Session, task_id: int) -> Task:
    task = tasks_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    return task


def _ensure_project(db: Session, project_id: int) -> None:
    if projects_service.get_project(db, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )


def _cycle_409(exc: tasks_service.TaskCycleError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _recurrence_422(exc: tasks_service.RecurrenceError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get("/projects/{project_id}/tasks", response_model=list[TaskRead])
def list_tasks(
    project_id: int,
    workflow_status: TaskWorkflowStatus | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    _ensure_project(db, project_id)
    return reads_with_blocked(
        db,
        tasks_service.list_tasks(
            db,
            project_id,
            review_status=TaskReviewStatus.accepted,
            workflow_status=workflow_status,
            exclude_done=workflow_status is None,
        ),
    )


# Server-side default cap for the otherwise-unbounded all-tasks read. The UI
# requests everything, so the cap is generous; a client that genuinely needs a
# different window passes ``limit``/``offset`` explicitly.
DEFAULT_TASK_LIMIT = 500
MAX_TASK_LIMIT = 1000


@router.get("/tasks", response_model=list[TaskRead])
def list_all_tasks(
    review_status: TaskReviewStatus | None = Query(
        default=TaskReviewStatus.accepted
    ),
    workflow_status: TaskWorkflowStatus | None = Query(default=None),
    limit: int = Query(default=DEFAULT_TASK_LIMIT, ge=1, le=MAX_TASK_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    return reads_with_blocked(
        db,
        tasks_service.list_tasks(
            db,
            review_status=review_status,
            workflow_status=workflow_status,
            exclude_done=workflow_status is None,
            limit=limit,
            offset=offset,
        ),
    )


@router.post("/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_unscoped_task(data: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    if data.project_id is not None:
        _ensure_project(db, data.project_id)
    try:
        task = tasks_service.create_task(
            db,
            project_id=data.project_id,
            title=data.title,
            description=data.description,
            review_status=data.review_status,
            workflow_status=data.workflow_status,
            priority=data.priority,
            due_date=data.due_date,
            parent_task_id=data.parent_task_id,
            estimated_minutes=data.estimated_minutes,
            assignee_hint=data.assignee_hint,
        )
    except tasks_service.TaskCycleError as exc:
        raise _cycle_409(exc) from exc
    db.commit()
    db.refresh(task)
    logger.info("task_created", task_id=task.id, project_id=task.project_id)
    return read_with_blocked(db, task)


@router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    project_id: int, data: TaskCreate, db: Session = Depends(get_db)
) -> TaskRead:
    _ensure_project(db, project_id)
    # The path project_id is authoritative here; any ``data.project_id`` is ignored.
    try:
        task = tasks_service.create_task(
            db,
            project_id=project_id,
            title=data.title,
            description=data.description,
            review_status=data.review_status,
            workflow_status=data.workflow_status,
            priority=data.priority,
            due_date=data.due_date,
            parent_task_id=data.parent_task_id,
            estimated_minutes=data.estimated_minutes,
            assignee_hint=data.assignee_hint,
        )
    except tasks_service.TaskCycleError as exc:
        raise _cycle_409(exc) from exc
    db.commit()
    db.refresh(task)
    logger.info("task_created", task_id=task.id, project_id=project_id)
    return read_with_blocked(db, task)


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    return read_with_blocked(db, _get_task_or_404(db, task_id))


@router.get("/tasks/{task_id}/subtasks", response_model=list[TaskRead])
def list_subtasks(task_id: int, db: Session = Depends(get_db)) -> list[TaskRead]:
    """Direct active children of a task, including candidates and done (unlike GET /api/tasks)."""
    _get_task_or_404(db, task_id)
    return reads_with_blocked(db, tasks_service.list_subtasks(db, task_id))


@router.post(
    "/tasks/{task_id}/break-down",
    response_model=list[TaskRead],
    dependencies=[
        Depends(
            rate_limit("task_breakdown", per_min_attr="rate_limit_breakdown_per_min")
        )
    ],
)
def break_down_task(task_id: int, db: Session = Depends(get_db)) -> list[TaskRead]:
    """Suggest subtasks for a task via the model, as candidate children.

    Idempotent: a task with a pending breakdown returns its existing candidates
    without a second model call. A model output that fails Pydantic validation is
    recorded as a training failure and surfaced as a 422 (prime directive #3).
    """
    task = _get_task_or_404(db, task_id)
    try:
        candidates = breakdown_workflow.break_down_task(db, task)
    except ValidationError as exc:
        logger.exception("breakdown_validation_failed", task_id=task_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The model returned an invalid breakdown",
        ) from exc
    except GatewayError as exc:
        # Ollama unreachable / timeout: report an upstream failure, never a 500.
        logger.error("breakdown_upstream_error", task_id=task_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="breakdown service unavailable — is Ollama running?",
        ) from exc
    logger.info(
        "task_broken_down", task_id=task_id, candidate_count=len(candidates)
    )
    return reads_with_blocked(db, candidates)


@router.post(
    "/tasks/{task_id}/breakdown/review", response_model=BreakdownReviewResult
)
def review_breakdown(
    task_id: int, data: BreakdownReviewRequest, db: Session = Depends(get_db)
) -> BreakdownReviewResult:
    """Approve/dismiss suggested subtasks; capture training once all are decided."""
    task = _get_task_or_404(db, task_id)
    try:
        result = breakdown_service.review_breakdown(db, task, data.decisions)
    except breakdown_service.AlreadyReviewedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    logger.info(
        "breakdown_review_recorded",
        task_id=task_id,
        approved=result.approved,
        dismissed=result.dismissed,
        finalized=result.finalized,
    )
    return result


@router.patch("/tasks/{task_id}", response_model=TaskRead)
def update_task(
    task_id: int, data: TaskUpdate, db: Session = Depends(get_db)
) -> TaskRead:
    task = _get_task_or_404(db, task_id)
    fields = data.model_dump(exclude_unset=True)
    # A non-null project_id must reference a real project (matches the POST routes).
    # An explicit null is allowed but does NOT un-file an accepted task: the service
    # rehomes any accepted task with no project back to General (accepted tasks are
    # always filed). Only a candidate task legitimately stays unfiled until review.
    if fields.get("project_id") is not None:
        _ensure_project(db, fields["project_id"])
    try:
        updated = tasks_service.update_task(db, task, fields)
    except tasks_service.TaskCycleError as exc:
        raise _cycle_409(exc) from exc
    except (tasks_service.DerivedStatusError, tasks_service.BlockedTaskError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except tasks_service.RecurrenceError as exc:
        raise _recurrence_422(exc) from exc

    db.commit()
    db.refresh(updated)
    logger.info("task_updated", task_id=updated.id)
    return read_with_blocked(db, updated)


@router.post("/tasks/{task_id}/done", response_model=TaskRead)
def mark_task_done(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = _get_task_or_404(db, task_id)
    try:
        updated = tasks_service.mark_done(db, task)
    except (tasks_service.DerivedStatusError, tasks_service.BlockedTaskError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(updated)
    logger.info("task_marked_done", task_id=updated.id)
    return read_with_blocked(db, updated)


@router.post("/tasks/{task_id}/skip", response_model=TaskRead)
def skip_occurrence(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    """Skip a recurring occurrence: soft-delete it, return the next occurrence."""
    task = _get_task_or_404(db, task_id)
    try:
        next_occurrence = task_recurrence.skip_occurrence(db, task)
    except tasks_service.RecurrenceError as exc:
        raise _recurrence_422(exc) from exc
    db.commit()
    db.refresh(next_occurrence)
    logger.info(
        "task_occurrence_skipped", task_id=task_id, next_task_id=next_occurrence.id
    )
    return read_with_blocked(db, next_occurrence)


@router.get("/tasks/{task_id}/series", response_model=TaskSeries)
def get_task_series(task_id: int, db: Session = Depends(get_db)) -> TaskSeries:
    """All occurrences in this task's recurrence series, oldest due date first."""
    task = _get_task_or_404(db, task_id)
    if task.recurrence_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Task is not part of a recurrence series",
        )
    occurrences = task_recurrence.get_series(db, task.recurrence_id)
    return TaskSeries(
        recurrence_id=task.recurrence_id,
        occurrences=reads_with_blocked(db, occurrences),
    )


@router.post("/tasks/{task_id}/stop-recurrence", response_model=TaskRead)
def stop_recurrence(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    """Stop a series from spawning further occurrences (clears repeat_interval)."""
    task = _get_task_or_404(db, task_id)
    try:
        updated = task_recurrence.stop_recurrence(db, task)
    except tasks_service.RecurrenceError as exc:
        raise _recurrence_422(exc) from exc
    db.commit()
    db.refresh(updated)
    logger.info("task_recurrence_stopped", task_id=updated.id)
    return read_with_blocked(db, updated)


@router.post("/tasks/{task_id}/reopen", response_model=TaskRead)
def reopen_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = _get_task_or_404(db, task_id)
    try:
        updated = tasks_service.reopen_task(db, task)
    except tasks_service.DerivedStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(updated)
    logger.info("task_reopened", task_id=updated.id)
    return read_with_blocked(db, updated)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)) -> None:
    task = _get_task_or_404(db, task_id)
    tasks_service.soft_delete_task(db, task)
    db.commit()
    logger.info("task_deleted", task_id=task_id)


@router.post("/tasks/{task_id}/restore", response_model=TaskRead)
def restore_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = task_trash.get_deleted_task(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No deleted task with that id",
        )
    restored = task_trash.restore_task(db, task)
    db.commit()
    db.refresh(restored)
    logger.info("task_restored", task_id=restored.id)
    return read_with_blocked(db, restored)


@router.delete(
    "/tasks/{task_id}/purge",
    status_code=status.HTTP_204_NO_CONTENT,
)
def purge_task(task_id: int, db: Session = Depends(get_db)) -> None:
    task = trashed_row_or_error(
        task_trash.get_deleted_task(db, task_id),
        lambda: tasks_service.get_task(db, task_id),
        conflict_detail="Task is not in trash; delete it first",
        absent_detail="No deleted task with that id",
    )
    task_trash.purge_task(db, task)
    db.commit()
    logger.info("task_purged", task_id=task_id)
