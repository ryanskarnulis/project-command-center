from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.workflows import break_down_task as breakdown_workflow
from app.db.models import Task, TaskReviewStatus, TaskWorkflowStatus
from app.db.session import get_db
from app.schemas.tasks import (
    BreakdownReviewRequest,
    BreakdownReviewResult,
    TaskCreate,
    TaskRead,
    TaskUpdate,
)
from app.services import breakdown as breakdown_service
from app.services import projects as projects_service
from app.services import task_dependencies as deps_service
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


def _read_with_blocked(db: Session, task: Task) -> TaskRead:
    """A single task's read model with its derived ``is_blocked`` populated."""
    return TaskRead.model_validate(task).model_copy(
        update={"is_blocked": deps_service.is_blocked(db, task.id)}
    )


def _reads_with_blocked(db: Session, tasks: Sequence[Task]) -> list[TaskRead]:
    """A task list with ``is_blocked`` resolved in one query (no N+1)."""
    blocked = deps_service.blocked_task_ids(db, [t.id for t in tasks])
    return [
        TaskRead.model_validate(t).model_copy(
            update={"is_blocked": t.id in blocked}
        )
        for t in tasks
    ]


@router.get("/projects/{project_id}/tasks", response_model=list[TaskRead])
def list_tasks(
    project_id: int,
    workflow_status: TaskWorkflowStatus | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    _ensure_project(db, project_id)
    return _reads_with_blocked(
        db,
        tasks_service.list_tasks(
            db,
            project_id,
            review_status=TaskReviewStatus.accepted,
            workflow_status=workflow_status,
            exclude_done=workflow_status is None,
        ),
    )


@router.get("/tasks", response_model=list[TaskRead])
def list_all_tasks(
    review_status: TaskReviewStatus | None = Query(
        default=TaskReviewStatus.accepted
    ),
    workflow_status: TaskWorkflowStatus | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    return _reads_with_blocked(
        db,
        tasks_service.list_tasks(
            db,
            review_status=review_status,
            workflow_status=workflow_status,
            exclude_done=workflow_status is None,
        ),
    )


@router.post("/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_unscoped_task(data: TaskCreate, db: Session = Depends(get_db)) -> Task:
    try:
        task = tasks_service.create_task(
            db,
            project_id=None,
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
    return task


@router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    project_id: int, data: TaskCreate, db: Session = Depends(get_db)
) -> Task:
    _ensure_project(db, project_id)
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
    return task


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    return _read_with_blocked(db, _get_task_or_404(db, task_id))


@router.get("/tasks/{task_id}/subtasks", response_model=list[TaskRead])
def list_subtasks(task_id: int, db: Session = Depends(get_db)) -> list[TaskRead]:
    """Direct active children of a task, including candidates and done (unlike GET /api/tasks)."""
    _get_task_or_404(db, task_id)
    return _reads_with_blocked(db, tasks_service.list_subtasks(db, task_id))


@router.post("/tasks/{task_id}/break-down", response_model=list[TaskRead])
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
    logger.info(
        "task_broken_down", task_id=task_id, candidate_count=len(candidates)
    )
    return _reads_with_blocked(db, candidates)


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
    try:
        updated = tasks_service.update_task(
            db, task, data.model_dump(exclude_unset=True)
        )
    except tasks_service.TaskCycleError as exc:
        raise _cycle_409(exc) from exc
    db.commit()
    db.refresh(updated)
    logger.info("task_updated", task_id=updated.id)
    return _read_with_blocked(db, updated)


@router.post("/tasks/{task_id}/done", response_model=TaskRead)
def mark_task_done(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = _get_task_or_404(db, task_id)
    updated = tasks_service.mark_done(db, task)
    db.commit()
    db.refresh(updated)
    logger.info("task_marked_done", task_id=updated.id)
    return _read_with_blocked(db, updated)


@router.post("/tasks/{task_id}/skip", response_model=TaskRead)
def skip_occurrence(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    """Skip a recurring occurrence: soft-delete it, return the next occurrence."""
    task = _get_task_or_404(db, task_id)
    next_occurrence = tasks_service.skip_occurrence(db, task)
    db.commit()
    db.refresh(next_occurrence)
    logger.info(
        "task_occurrence_skipped", task_id=task_id, next_task_id=next_occurrence.id
    )
    return _read_with_blocked(db, next_occurrence)


@router.post("/tasks/{task_id}/reopen", response_model=TaskRead)
def reopen_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    task = _get_task_or_404(db, task_id)
    updated = tasks_service.reopen_task(db, task)
    db.commit()
    db.refresh(updated)
    logger.info("task_reopened", task_id=updated.id)
    return _read_with_blocked(db, updated)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)) -> None:
    task = _get_task_or_404(db, task_id)
    tasks_service.soft_delete_task(db, task)
    db.commit()
    logger.info("task_deleted", task_id=task_id)


@router.post("/tasks/{task_id}/restore", response_model=TaskRead)
def restore_task(task_id: int, db: Session = Depends(get_db)) -> Task:
    task = tasks_service.get_deleted_task(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No deleted task with that id",
        )
    restored = tasks_service.restore_task(db, task)
    db.commit()
    db.refresh(restored)
    logger.info("task_restored", task_id=restored.id)
    return restored


@router.delete("/tasks/{task_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_task(task_id: int, db: Session = Depends(get_db)) -> None:
    task = tasks_service.get_deleted_task(db, task_id)
    if task is None:
        # Distinguish an active task (exists, not in trash → 409) from a truly
        # absent one (404): purge only ever touches rows already in trash.
        if tasks_service.get_task(db, task_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Task is not in trash; delete it first",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No deleted task with that id",
        )
    tasks_service.purge_task(db, task)
    db.commit()
    logger.info("task_purged", task_id=task_id)
