from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from sqlalchemy import func, select

from app.db.models import Project, Task
from app.services import projects as projects_service
from app.services import task_trash
from app.services.common import count_deleted, deleted


@dataclass(frozen=True)
class PurgeCounts:
    """How many trashed rows ``empty_trash`` permanently removed, per kind."""

    projects: int
    tasks: int


@dataclass(frozen=True)
class TrashCounts:
    """Counts for the trash UI.

    ``projects`` / ``tasks`` are the badge/heading totals — soft-deleted projects
    and *standalone* soft-deleted tasks (cascade tasks come back with their
    project, so they don't show as their own trash rows). ``purge_total`` is the
    exact number of rows ``empty_trash`` would permanently remove, so the
    Empty-trash confirm can name a figure that matches the actual deletion (it
    includes cascade tasks and excludes protected projects).
    """

    projects: int
    tasks: int
    purge_total: int


def count_trash(db: Session) -> TrashCounts:
    """Exact trash counts for the nav badge and the Empty-trash confirm.

    Uses ``COUNT(*)`` rather than the length of the paginated ``/trash`` lists, so
    the numbers stay correct past the list's page limit.
    """
    # Projects ``empty_trash`` would actually purge: soft-deleted and not
    # protected (mirrors the ``if not p.is_protected`` skip in ``purge_selected``;
    # ``is_protected`` == ``system_key is not None``).
    purgeable_projects = (
        db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.deleted_at.is_not(None), Project.system_key.is_(None))
        )
        or 0
    )
    return TrashCounts(
        projects=count_deleted(db, Project),
        # Tasks cascade-deleted with their project aren't standalone trash rows
        # (they come back with the project), so they don't count toward the badge.
        tasks=task_trash.count_standalone_deleted_tasks(db),
        # Everything ``empty_trash`` removes: all soft-deleted tasks (incl. cascade
        # tasks, unlike the badge) plus the purgeable (non-protected) projects.
        purge_total=count_deleted(db, Task) + purgeable_projects,
    )


def purge_selected(
    db: Session,
    *,
    project_ids: Sequence[int],
    task_ids: Sequence[int],
) -> PurgeCounts:
    """Permanently delete the given trashed rows. Ids not in trash are skipped.

    Ordered so each step's FK cleanup doesn't fight the next: soft-deleted tasks
    first, then projects (whose owned trashed tasks are already gone, so only
    the nullable project FKs remain). The protected ``General`` project is never
    purged.

    Ids are resolved against trash up front, then each purge re-fetches and skips
    rows a prior cascade already took: purging a parent task takes its whole
    subtree, so a child selected alongside its parent is already gone by the time
    its turn comes. It still counts as removed — it *was* removed, by the
    ancestor's purge — which is why the counts come from the up-front snapshot
    rather than the per-row loop. Ids that were never in trash are filtered out
    by that snapshot and count for nothing. Caller commits.
    """
    purge_task_ids = [
        t.id
        for t in db.execute(deleted(Task).where(Task.id.in_(task_ids))).scalars()
    ]
    purge_project_ids = [
        p.id
        for p in db.execute(
            deleted(Project).where(Project.id.in_(project_ids))
        ).scalars()
        if not p.is_protected
    ]

    for task_id in purge_task_ids:
        task = db.execute(
            deleted(Task).where(Task.id == task_id)
        ).scalar_one_or_none()
        if task is not None:
            task_trash.purge_task(db, task)

    for project_id in purge_project_ids:
        project = db.execute(
            deleted(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if project is not None:
            projects_service.purge_project(db, project)

    return PurgeCounts(
        projects=len(purge_project_ids),
        tasks=len(purge_task_ids),
    )


def empty_trash(db: Session) -> PurgeCounts:
    """Permanently delete everything in trash. Idempotent (re-running clears 0).

    Snapshots every trashed id and hands it to ``purge_selected``, which owns the
    ordering, the protected-project rule, and the already-cascaded skip. Caller
    commits.
    """
    return purge_selected(
        db,
        project_ids=[p.id for p in db.execute(deleted(Project)).scalars()],
        task_ids=[t.id for t in db.execute(deleted(Task)).scalars()],
    )
