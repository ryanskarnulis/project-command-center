from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from sqlalchemy import func, select

from app.db.models import Project, Task
from app.services import projects as projects_service
from app.services import task_trash
from app.services.common import chunked, count_deleted, deleted


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


def _removed_task_ids(
    db: Session,
    *,
    task_ids: Sequence[int],
    project_ids: Sequence[int],
) -> set[int]:
    """Every task row a purge of ``task_ids`` + ``project_ids`` would destroy.

    Must be called *before* the purge runs — it reads the rows that are about to
    go. The roots are the selected trashed tasks plus every task archived with a
    purged project; each root expands to the soft-deleted subtree its own purge
    takes. Returned as a set so overlapping reachability (a task selected next to
    its project, or next to an ancestor) is counted once, matching the number of
    rows that actually disappear.
    """
    roots = list(task_ids)
    # Chunked: ``project_ids`` can be every trashed project (``empty_trash``), and
    # one bound parameter per id overruns SQLite's ceiling (issue #275). No empty
    # guard needed — ``chunked`` yields nothing for an empty list.
    for chunk in chunked(project_ids):
        roots.extend(
            db.execute(
                select(Task.id).where(
                    Task.deleted_at.is_not(None), Task.project_id.in_(chunk)
                )
            ).scalars()
        )

    removed: set[int] = set()
    for root_id in roots:
        # A root already inside a previously expanded subtree adds nothing: the
        # subtree of a descendant is contained in its ancestor's.
        if root_id in removed:
            continue
        task = db.execute(
            deleted(Task).where(Task.id == root_id)
        ).scalar_one_or_none()
        if task is not None:
            removed.update(task_trash.deleted_subtree_ids(db, task))
    return removed


def _trashed_task_ids(db: Session, task_ids: Sequence[int]) -> list[int]:
    """Which of ``task_ids`` are really in the trash, resolved in ``IN`` chunks.

    The chunking is what lets ``empty_trash`` work at any size (issue #275): its
    list is however many rows sit in the trash, so unlike ``POST /api/trash/purge``
    no request schema can bound it, and one bound parameter per id blew past
    SQLite's 32,766-parameter ceiling on the very first statement of the purge —
    an unhandled ``OperationalError`` that left the trash permanently unemptiable.
    """
    resolved: list[int] = []
    for chunk in chunked(task_ids):
        resolved.extend(
            task.id
            for task in db.execute(deleted(Task).where(Task.id.in_(chunk))).scalars()
        )
    return resolved


def _purgeable_project_ids(db: Session, project_ids: Sequence[int]) -> list[int]:
    """Which of ``project_ids`` a purge may actually destroy, in ``IN`` chunks.

    Trashed *and* not protected: ``is_protected`` stays the single source of
    truth for the ``General`` rule, which is why this loads the rows rather than
    re-deriving the condition in SQL. Chunked for the same reason as
    ``_trashed_task_ids``.
    """
    return [
        project.id
        for chunk in chunked(project_ids)
        for project in db.execute(
            deleted(Project).where(Project.id.in_(chunk))
        ).scalars()
        if not project.is_protected
    ]


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

    Ids are resolved against trash up front — in ``IN`` chunks, so the id lists
    themselves have no size limit (issue #275) — then each purge re-fetches and skips
    rows a prior cascade already took: purging a parent task takes its whole
    subtree, so a child selected alongside its parent is already gone by the time
    its turn comes. It still counts as removed — it *was* removed, by the
    ancestor's purge — which is why the counts come from the up-front snapshot
    rather than the per-row loop. Ids that were never in trash are filtered out
    by that snapshot and count for nothing. Caller commits.

    The reported ``tasks`` count is the *set* of rows the purge really destroys
    (BUG #184): explicitly selected tasks, their cascade-purged subtrees, and the
    tasks archived with each purged project. A set, so a task reachable more than
    one way — selected alongside its own project, or alongside an ancestor —
    counts exactly once.
    """
    purge_task_ids = _trashed_task_ids(db, task_ids)
    purge_project_ids = _purgeable_project_ids(db, project_ids)

    removed_task_ids = _removed_task_ids(
        db, task_ids=purge_task_ids, project_ids=purge_project_ids
    )

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
        tasks=len(removed_task_ids),
    )


def empty_trash(db: Session) -> PurgeCounts:
    """Permanently delete everything in trash. Idempotent (re-running clears 0).

    Snapshots every trashed id and hands it to ``purge_selected``, which owns the
    ordering, the protected-project rule, and the already-cascaded skip. Caller
    commits.

    These lists are DB-sourced, so no request schema stands between them and the
    SQL — ``MAX_PURGE_IDS`` bounds what a caller may *send* to
    ``POST /api/trash/purge``, and structurally cannot bound what happens to be
    sitting in the trash. That is why the size fix lives in ``purge_selected``'s
    chunked id resolution rather than at a boundary here (issue #275).
    """
    return purge_selected(
        db,
        project_ids=[p.id for p in db.execute(deleted(Project)).scalars()],
        task_ids=[t.id for t in db.execute(deleted(Task)).scalars()],
    )
