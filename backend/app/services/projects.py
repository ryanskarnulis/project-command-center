from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, Project, Task, utcnow
from app.services import activity
from app.services.common import active, deleted, hard_delete, restore, soft_delete

DEFAULT_PROJECT_NAME = "General"
DEFAULT_PROJECT_DESCRIPTION = "Default project for unfiled tasks"
DEFAULT_PROJECT_SYSTEM_KEY = "general"


def list_projects(db: Session, *, include_closed: bool = False) -> Sequence[Project]:
    stmt = active(Project)
    if not include_closed:
        stmt = stmt.where(Project.closed_at.is_(None))
    return db.execute(stmt.order_by(Project.sort_order, Project.id)).scalars().all()


def get_project(db: Session, project_id: int) -> Project | None:
    return db.execute(
        active(Project).where(Project.id == project_id)
    ).scalar_one_or_none()


def create_project(db: Session, *, name: str, description: str | None = None) -> Project:
    # New projects land at the end of the manual order.
    next_order = db.execute(
        select(func.coalesce(func.max(Project.sort_order), 0) + 1)
    ).scalar_one()
    project = Project(name=name, description=description, sort_order=next_order)
    db.add(project)
    db.flush()
    db.refresh(project)
    activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="created",
        summary=f'Project "{project.name}" created',
    )
    return project


def get_default_project(db: Session) -> Project | None:
    return db.execute(
        active(Project).where(Project.system_key == DEFAULT_PROJECT_SYSTEM_KEY)
    ).scalar_one_or_none()


def ensure_default_project(db: Session) -> Project:
    """Return General, adopting or creating it if it's somehow missing.

    The real seed is migration ``4f2c8b7d0a1e``, which runs before any request is
    served; General is protected, so nothing can close, trash, or purge it. This is
    a fallback for a database that never saw that migration. Names aren't unique, so
    the adoption lookup takes the oldest match rather than assuming there's one.
    """
    project = get_default_project(db)
    if project is not None:
        return project

    project = (
        db.execute(
            active(Project)
            .where(Project.name == DEFAULT_PROJECT_NAME, Project.system_key.is_(None))
            .order_by(Project.id)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if project is None:
        project = Project(
            name=DEFAULT_PROJECT_NAME,
            description=DEFAULT_PROJECT_DESCRIPTION,
            system_key=DEFAULT_PROJECT_SYSTEM_KEY,
        )
        db.add(project)
    else:
        project.system_key = DEFAULT_PROJECT_SYSTEM_KEY
        if project.description is None:
            project.description = DEFAULT_PROJECT_DESCRIPTION

    db.flush()
    db.refresh(project)
    return project


def ensure_default_project_id(db: Session) -> int:
    return ensure_default_project(db).id


def update_project(db: Session, project: Project, fields: Mapping[str, Any]) -> Project:
    for key, value in fields.items():
        setattr(project, key, value)
    db.flush()
    db.refresh(project)
    activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="updated",
        summary=f'Project "{project.name}" updated',
    )
    return project


def close_project(db: Session, project: Project) -> Project:
    """Close (archive) a project: hidden from the default list, tasks untouched.

    Unlike a soft delete this is instantly reversible via ``reopen_project`` and
    never cascades to tasks. Protected projects (General) can't be closed — they
    are the landing spot for unfiled tasks.
    """
    if project.is_protected:
        raise ValueError(f'Project "{project.name}" is protected and cannot be closed')
    if project.closed_at is None:
        project.closed_at = utcnow()
        db.flush()
        activity.record_event(
            db,
            project_id=project.id,
            entity_type="project",
            entity_id=project.id,
            action="closed",
            summary=f'Project "{project.name}" closed',
        )
    return project


def reopen_project(db: Session, project: Project) -> Project:
    if project.closed_at is not None:
        project.closed_at = None
        # A reopened project rejoins the manual order at the end, like a new one.
        project.sort_order = db.execute(
            select(func.coalesce(func.max(Project.sort_order), 0) + 1)
        ).scalar_one()
        db.flush()
        activity.record_event(
            db,
            project_id=project.id,
            entity_type="project",
            entity_id=project.id,
            action="reopened",
            summary=f'Project "{project.name}" reopened',
        )
    return project


def reorder_projects(db: Session, ordered_ids: Sequence[int]) -> Sequence[Project]:
    """Set the manual project order to ``ordered_ids`` (all open active projects).

    Requires the full active set so a stale client can't silently drop a
    project to the front/back; raises ValueError on any mismatch.
    """
    projects = list_projects(db)
    if sorted(ordered_ids) != sorted(project.id for project in projects):
        raise ValueError("ordered_ids must be exactly the open project ids")

    by_id = {project.id: project for project in projects}
    for position, project_id in enumerate(ordered_ids, start=1):
        by_id[project_id].sort_order = position
    db.flush()
    # One event for the whole reorder; entity_id 0 because no single project
    # owns it and the log schema has no batch notion.
    activity.record_event(
        db,
        project_id=None,
        entity_type="project",
        entity_id=0,
        action="reordered",
        summary="Projects reordered",
    )
    return list_projects(db)


def _mark_subtree_deleted_with_project(
    db: Session, task: Task, project_id: int
) -> None:
    """Stamp ``task`` and its whole active subtree with the owning project id.

    Stamped before the cascade soft-delete so ``restore_project`` can bring back
    exactly the set that the project deletion removed (and ``list_deleted_tasks``
    can hide it from the standalone Tasks trash section).
    """
    from app.services import tasks as tasks_service

    task.deleted_with_project_id = project_id
    for child in tasks_service.list_subtasks(db, task.id):
        _mark_subtree_deleted_with_project(db, child, project_id)


def soft_delete_project(db: Session, project: Project) -> None:
    if project.is_protected:
        raise ValueError(f'Project "{project.name}" is protected and cannot be deleted')

    # Cascade: a deleted project takes its tasks (and their subtrees) into the
    # trash with it, instead of rehoming them to General. Restore offers to bring
    # them back (see restore_project). Tasks already trashed independently keep
    # their null marker and are left untouched.
    from app.services import tasks as tasks_service

    top_level = db.execute(
        active(Task).where(
            Task.project_id == project.id, Task.parent_task_id.is_(None)
        )
    ).scalars().all()
    for task in top_level:
        _mark_subtree_deleted_with_project(db, task, project.id)
        tasks_service.soft_delete_task(db, task)

    # Safety sweep: any task still active in this project (e.g. a subtask whose
    # parent lives in a different project, so the cascade above never reached it).
    # Same shape as the pass above — stamp the whole subtree, then cascade —
    # because the invariant is that every row this deletion removes is stamped,
    # and a swept task's children can sit outside this project (a cross-project
    # child is supported: create_task only inherits the parent's project when
    # none is given, and re-parenting never moves it). Stamping them with the
    # deleted project is what the top-level pass already does to its own
    # cross-project descendants.
    #
    # The row list is read up front, so a task an earlier iteration's cascade
    # already deleted is skipped rather than soft-deleted twice — soft_delete
    # re-stamps deleted_at unconditionally and the event log would fire again.
    for task in db.execute(
        active(Task).where(Task.project_id == project.id)
    ).scalars().all():
        if task.deleted_at is not None:
            continue
        _mark_subtree_deleted_with_project(db, task, project.id)
        tasks_service.soft_delete_task(db, task)

    db.flush()
    soft_delete(project)
    db.flush()
    activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="deleted",
        summary=f'Project "{project.name}" deleted',
    )


# --- Trash / restore (Sprint 7) --------------------------------------------


def list_deleted_projects(db: Session, *, limit: int = 50) -> Sequence[Project]:
    """Soft-deleted projects, most-recently-deleted first."""
    return (
        db.execute(deleted(Project).order_by(Project.deleted_at.desc()).limit(limit))
        .scalars()
        .all()
    )


def get_deleted_project(db: Session, project_id: int) -> Project | None:
    return db.execute(
        deleted(Project).where(Project.id == project_id)
    ).scalar_one_or_none()


def count_tasks_deleted_with_project(db: Session, project_id: int) -> int:
    """How many trashed tasks would come back if this project is restored with them."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.deleted_at.is_not(None),
                Task.deleted_with_project_id == project_id,
            )
        )
        or 0
    )


def restore_project(
    db: Session, project: Project, *, restore_tasks: bool = False
) -> tuple[Project, int]:
    """Restore a trashed project; optionally bring back its cascade-deleted tasks.

    Returns ``(project, restored_task_count)``. Only tasks stamped with this
    project's id at delete time are pulled back — tasks the user trashed
    independently keep their null marker and stay in the trash.
    """
    restore(project)

    restored_tasks = 0
    if restore_tasks:
        tasks = db.execute(
            deleted(Task).where(Task.deleted_with_project_id == project.id)
        ).scalars().all()
        for task in tasks:
            restore(task)
            task.deleted_with_project_id = None
            restored_tasks += 1

    db.flush()
    db.refresh(project)
    activity.record_event(
        db,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        action="restored",
        summary=f'Project "{project.name}" restored',
    )
    return project, restored_tasks


# --- Permanent delete / purge (Sprint 9f) ----------------------------------


def purge_project(db: Session, project: Project) -> None:
    """Permanently delete a trashed project and clean every FK edge into it.

    Protected (``General``) is never purgeable. A deleted project's tasks are
    cascade-soft-deleted with it (they keep ``project_id``), so every task still
    pointing here is purged (via ``purge_task`` so their dependency/subtree edges
    go too). The nullable FKs that would otherwise
    dangle — ``activity_events.project_id`` (the audit log, kept but with the ref
    cleared) and any ``tasks.deleted_with_project_id`` still pointing here — are
    nulled.
    ``hard_delete``'s guard enforces the project is already in trash. Caller commits.
    """
    from app.services import task_trash  # local: avoid circular import

    if project.is_protected:
        raise ValueError(f'Project "{project.name}" is protected and cannot be deleted')

    # Purge every soft-deleted task still pointing here. A task may already be gone
    # when we reach it (a prior root's subtree purge took it), so re-fetch and skip
    # the misses rather than holding stale ORM rows.
    owned_ids = [
        t.id
        for t in db.execute(
            deleted(Task).where(Task.project_id == project.id)
        ).scalars()
    ]
    for task_id in owned_ids:
        task = db.execute(
            deleted(Task).where(Task.id == task_id)
        ).scalar_one_or_none()
        if task is not None:
            task_trash.purge_task(db, task)

    db.execute(
        update(ActivityEvent)
        .where(ActivityEvent.project_id == project.id)
        .values(project_id=None)
    )
    db.execute(
        update(Task)
        .where(Task.deleted_with_project_id == project.id)
        .values(deleted_with_project_id=None)
    )

    hard_delete(db, project)
