from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.task_reads import read_with_blocked
from app.db.session import get_db, get_db_write
from app.schemas.common import EntityId
from app.schemas.trash import (
    EmptyTrashResult,
    ProjectRestoreUndo,
    ProjectTrashRead,
    PurgeSelectedRequest,
    TaskRestoreResult,
    TaskRestoreUndo,
    TrashCountResult,
    TrashRead,
)
from app.services import projects as projects_service
from app.services import task_trash
from app.services import tasks as tasks_service
from app.services import trash as trash_service
from app.services.common import RestoreUndoConflictError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/trash", tags=["trash"])


@router.get("", response_model=TrashRead)
def get_trash(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> TrashRead:
    """Recently soft-deleted projects and tasks — the restore view."""
    deleted_projects = projects_service.list_deleted_projects(db, limit=limit)
    return TrashRead(
        projects=[
            ProjectTrashRead.model_validate(p).model_copy(
                update={
                    "archived_task_count": projects_service.count_tasks_deleted_with_project(
                        db, p.id
                    ),
                    "purge_task_count": projects_service.count_tasks_purged_with_project(
                        db, p.id
                    ),
                }
            )
            for p in deleted_projects
        ],
        tasks=task_trash.list_deleted_tasks(db, limit=limit),  # type: ignore[arg-type]
    )


@router.get("/count", response_model=TrashCountResult)
def get_trash_count(db: Session = Depends(get_db)) -> TrashCountResult:
    """Exact per-kind trash counts for the nav badge (unbounded by the list page)."""
    counts = trash_service.count_trash(db)
    return TrashCountResult(
        projects=counts.projects,
        tasks=counts.tasks,
        purge_total=counts.purge_total,
    )


@router.post("/tasks/{task_id}/restore", response_model=TaskRestoreResult)
def restore_task(
    task_id: EntityId, db: Session = Depends(get_db_write)
) -> TaskRestoreResult:
    """Restore a trashed task and hand back the receipt that undoes it.

    The same restore as ``POST /api/tasks/{id}/restore`` (root-only; a skipped
    occurrence un-skips), plus ``undo``: what this restore changed, for
    ``POST /api/trash/tasks/undo-restore`` to reverse (#306, #307). The Trash
    page uses this route because it is the surface that offers Undo.
    """
    task = task_trash.get_deleted_task(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No deleted task with that id",
        )
    try:
        restored, undo = task_trash.restore_task_with_undo(db, task)
    except tasks_service.OccurrenceConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(restored)
    logger.info("trash_task_restored", task_id=task_id, restored_id=restored.id)
    return TaskRestoreResult(task=read_with_blocked(db, restored), undo=undo)


@router.post("/tasks/undo-restore", status_code=status.HTTP_204_NO_CONTENT)
def undo_task_restore(
    undo: TaskRestoreUndo, db: Session = Depends(get_db_write)
) -> None:
    """Reverse exactly what one Trash task restore changed.

    Not ``DELETE /api/tasks/{id}``: that cascades through every active
    descendant, including ones restored on their own before this restore
    (#307), and an un-skip's inverse is moving the series back (#306). 409 when
    the rows have moved on since the restore.
    """
    try:
        task_trash.undo_task_restore(db, undo)
    except RestoreUndoConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    logger.info("trash_task_restore_undone", task_id=undo.task_id)


@router.post("/projects/undo-restore", status_code=status.HTTP_204_NO_CONTENT)
def undo_project_restore(
    undo: ProjectRestoreUndo, db: Session = Depends(get_db_write)
) -> None:
    """Reverse exactly what one project restore changed; 409 if it moved on."""
    try:
        projects_service.undo_project_restore(db, undo)
    except RestoreUndoConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    logger.info("trash_project_restore_undone", project_id=undo.project_id)


@router.post("/purge", response_model=EmptyTrashResult)
def purge_selected(
    payload: PurgeSelectedRequest,
    db: Session = Depends(get_db_write),
) -> EmptyTrashResult:
    """Permanently delete the selected trashed rows, in one transaction.

    Skips ids that aren't in trash instead of 404ing: for a bulk purge the end
    state is what matters, and the caller's own cascade is the usual reason an id
    is already gone. The single-item purge routes keep their 404.
    """
    counts = trash_service.purge_selected(
        db,
        project_ids=payload.project_ids,
        task_ids=payload.task_ids,
    )
    db.commit()
    logger.info(
        "trash_purged_selected",
        projects=counts.projects,
        tasks=counts.tasks,
        requested_projects=len(payload.project_ids),
        requested_tasks=len(payload.task_ids),
    )
    return EmptyTrashResult(
        projects=counts.projects,
        tasks=counts.tasks,
    )


@router.delete(
    "",
    response_model=EmptyTrashResult,
)
def empty_trash(db: Session = Depends(get_db_write)) -> EmptyTrashResult:
    """Permanently delete every trashed row. Idempotent; protected projects spared."""
    counts = trash_service.empty_trash(db)
    db.commit()
    logger.info(
        "trash_emptied",
        projects=counts.projects,
        tasks=counts.tasks,
    )
    return EmptyTrashResult(
        projects=counts.projects,
        tasks=counts.tasks,
    )
