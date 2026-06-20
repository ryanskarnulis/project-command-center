from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import AITrainingExample, InboxItem, Project, Task
from app.services import inbox as inbox_service
from app.services import projects as projects_service
from app.services import tasks as tasks_service
from app.services import training_data as training_service
from app.services.common import count_deleted, deleted


@dataclass(frozen=True)
class PurgeCounts:
    """How many trashed rows ``empty_trash`` permanently removed, per kind."""

    projects: int
    tasks: int
    inbox_items: int
    training_examples: int


def count_trash(db: Session) -> PurgeCounts:
    """Exact per-kind counts of everything currently in trash (for the nav badge).

    Uses ``COUNT(*)`` rather than the length of the paginated ``/trash`` lists, so
    the badge stays correct past the list's page limit.
    """
    return PurgeCounts(
        projects=count_deleted(db, Project),
        tasks=count_deleted(db, Task),
        inbox_items=count_deleted(db, InboxItem),
        training_examples=count_deleted(db, AITrainingExample),
    )


def empty_trash(db: Session) -> PurgeCounts:
    """Permanently delete everything in trash. Idempotent (re-running clears 0).

    Ordered so each step's FK cleanup doesn't fight the next: inbox items first
    (purges/detaches their candidate tasks), then any remaining soft-deleted tasks,
    then projects (whose owned trashed tasks are already gone, so only aliases and
    the nullable project FKs remain). The protected ``General`` project is never
    purged. Ids are snapshotted up front — every trashed row is removed exactly
    once (some as part of an ancestor's subtree), so the snapshot sizes are the
    true removed counts; each purge re-fetches and skips rows a prior cascade
    already took. Caller commits.
    """
    inbox_ids = [i.id for i in db.execute(deleted(InboxItem)).scalars()]
    task_ids = [t.id for t in db.execute(deleted(Task)).scalars()]
    project_ids = [
        p.id
        for p in db.execute(deleted(Project)).scalars()
        if not p.is_protected
    ]
    # Training examples are a leaf table — purge order doesn't matter for them.
    training_ids = [e.id for e in db.execute(deleted(AITrainingExample)).scalars()]

    for inbox_id in inbox_ids:
        item = db.execute(
            deleted(InboxItem).where(InboxItem.id == inbox_id)
        ).scalar_one_or_none()
        if item is not None:
            inbox_service.purge_inbox_item(db, item)

    for task_id in task_ids:
        task = db.execute(
            deleted(Task).where(Task.id == task_id)
        ).scalar_one_or_none()
        if task is not None:
            tasks_service.purge_task(db, task)

    for project_id in project_ids:
        project = db.execute(
            deleted(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if project is not None:
            projects_service.purge_project(db, project)

    for example_id in training_ids:
        example = training_service.get_deleted_example(db, example_id)
        if example is not None:
            training_service.purge_example(db, example)

    return PurgeCounts(
        projects=len(project_ids),
        tasks=len(task_ids),
        inbox_items=len(inbox_ids),
        training_examples=len(training_ids),
    )
