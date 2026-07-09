from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import Task, TaskDependency
from app.services import projects as projects_service
from app.services.common import active, deleted, hard_delete, restore
from app.services.task_recurrence import reschedule_occurrence
from app.services.tasks import log_task_event


def list_deleted_tasks(db: Session, *, limit: int = 50) -> Sequence[Task]:
    """Soft-deleted tasks, most-recently-deleted first.

    Excludes tasks cascade-deleted with their project — those belong to the
    project's trash entry and are restored with it, not as standalone rows.
    """
    return (
        db.execute(
            deleted(Task)
            .where(Task.deleted_with_project_id.is_(None))
            .order_by(Task.deleted_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def count_standalone_deleted_tasks(db: Session) -> int:
    """Trashed tasks that are independently restorable (not cascade-deleted with a project)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.deleted_at.is_not(None),
                Task.deleted_with_project_id.is_(None),
            )
        )
        or 0
    )


def get_deleted_task(db: Session, task_id: int) -> Task | None:
    return db.execute(
        deleted(Task).where(Task.id == task_id)
    ).scalar_one_or_none()


def restore_task(db: Session, task: Task) -> Task:
    # Un-skip: if this occurrence's series still has a live occurrence, restoring
    # must NOT add a second one (that's the duplicate-series bug). Pull the live
    # occurrence's date (and its subtasks') back to the restored occurrence's date,
    # then hard-delete the restored row — the series resumes at the un-skipped date
    # with exactly one live occurrence.
    if task.recurrence_id is not None and task.due_date is not None:
        # The occurrence that replaced this one when it was skipped: the earliest
        # active sibling due on or after it. Filtering by date avoids retargeting an
        # earlier, already-completed occurrence (e.g. a done checklist parent).
        live = (
            db.execute(
                active(Task)
                .where(
                    Task.recurrence_id == task.recurrence_id,
                    Task.id != task.id,
                    Task.due_date >= task.due_date,
                )
                .order_by(Task.due_date.asc(), Task.id.asc())
            )
            .scalars()
            .first()
        )
        if live is not None:
            reschedule_occurrence(db, live, task.due_date)
            purge_task(db, task)
            db.flush()
            db.refresh(live)
            log_task_event(db, live, "restored")
            return live

    # Fallback (non-recurring, or a series with no live occurrence): plain restore.
    # A restored task may point at a since-deleted project; rehome it to General
    # so it stays reachable, mirroring the project-delete rehoming rule.
    if (
        task.project_id is not None
        and projects_service.get_project(db, task.project_id) is None
    ):
        task.project_id = projects_service.ensure_default_project_id(db)
    # An individually-restored task drops its project-cascade marker.
    task.deleted_with_project_id = None
    restore(task)
    db.flush()
    db.refresh(task)
    log_task_event(db, task, "restored")
    return task


def _deleted_subtree_depth_first(db: Session, task: Task) -> list[Task]:
    """The soft-deleted subtree rooted at ``task``, children before parents.

    Soft-deleting a parent cascade-soft-deletes its subtree, so the whole subtree
    sits in trash together; purging the root must take the descendants with it or
    they'd dangle a ``parent_task_id`` at a destroyed row. FK enforcement is on
    (``PRAGMA foreign_keys = ON``), so that dangle would *raise*; children-first
    ordering lets the caller delete in a single pass without tripping the
    self-referential FK.
    """
    children = (
        db.execute(deleted(Task).where(Task.parent_task_id == task.id))
        .scalars()
        .all()
    )
    ordered: list[Task] = []
    for child in children:
        ordered.extend(_deleted_subtree_depth_first(db, child))
    ordered.append(task)
    return ordered


def purge_task(db: Session, task: Task) -> None:
    """Permanently delete a trashed task and its soft-deleted subtree.

    Cleans the real FK edges first: dependency rows on either side of any subtree
    task, and any stray ``parent_task_id`` from a row outside the purge set (e.g. a
    child that was individually restored while its parent stayed in trash). The
    caller is responsible for committing.
    """
    subtree = _deleted_subtree_depth_first(db, task)
    ids = [t.id for t in subtree]

    db.execute(
        sql_delete(TaskDependency).where(
            or_(
                TaskDependency.task_id.in_(ids),
                TaskDependency.depends_on_task_id.in_(ids),
            )
        )
    )
    # Detach any row (active or not) still pointing into the purge set but not
    # itself being purged, so no dangling parent ref survives.
    db.execute(
        update(Task)
        .where(Task.parent_task_id.in_(ids), Task.id.not_in(ids))
        .values(parent_task_id=None)
    )

    for node in subtree:  # children before parents
        hard_delete(db, node)
