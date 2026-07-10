from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Project, Task
from app.services import projects as projects_service
from app.services import task_trash
from app.services.common import count_deleted, deleted


@dataclass(frozen=True)
class PurgeCounts:
    """How many trashed rows ``empty_trash`` permanently removed, per kind."""

    projects: int
    tasks: int


def count_trash(db: Session) -> PurgeCounts:
    """Exact per-kind counts of everything currently in trash (for the nav badge).

    Uses ``COUNT(*)`` rather than the length of the paginated ``/trash`` lists, so
    the badge stays correct past the list's page limit.
    """
    return PurgeCounts(
        projects=count_deleted(db, Project),
        # Tasks cascade-deleted with their project aren't standalone trash rows
        # (they come back with the project), so they don't count toward the badge.
        tasks=task_trash.count_standalone_deleted_tasks(db),
    )


def empty_trash(db: Session) -> PurgeCounts:
    """Permanently delete everything in trash. Idempotent (re-running clears 0).

    Ordered so each step's FK cleanup doesn't fight the next: soft-deleted tasks
    first, then projects (whose owned trashed tasks are already gone, so only
    the nullable project FKs remain). The protected ``General``
    project is never purged. Ids are snapshotted up front — every trashed row is
    removed exactly once (some as part of an ancestor's subtree), so the snapshot
    sizes are the true removed counts; each purge re-fetches and skips rows a
    prior cascade already took. Caller commits.
    """
    task_ids = [t.id for t in db.execute(deleted(Task)).scalars()]
    project_ids = [
        p.id
        for p in db.execute(deleted(Project)).scalars()
        if not p.is_protected
    ]

    for task_id in task_ids:
        task = db.execute(
            deleted(Task).where(Task.id == task_id)
        ).scalar_one_or_none()
        if task is not None:
            task_trash.purge_task(db, task)

    for project_id in project_ids:
        project = db.execute(
            deleted(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if project is not None:
            projects_service.purge_project(db, project)

    return PurgeCounts(
        projects=len(project_ids),
        tasks=len(task_ids),
    )
