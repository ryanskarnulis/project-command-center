from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.common import EntityId, MutationModel
from app.schemas.projects import ProjectRead
from app.schemas.tasks import TaskRead

# ``purge_selected`` expands each id list into a SQL ``IN (...)``, so every id
# costs one bound parameter. SQLite's compiled-in ceiling is 32,766 of them;
# past it the driver raises ``OperationalError: too many SQL variables`` and the
# request 500s on a LAN-reachable endpoint (#264). Bound the lists here so an
# oversized selection is a documented 422 before any SQL runs — the same
# boundary-rejection shape as #182 (out-of-range ids) and #235 (oversized
# offsets).
#
# 10,000 is deliberately generous: /trash pages at most 200 rows of each kind,
# so no selection the UI can build comes close, while the cap still leaves ample
# head-room under 32,766 for the other parameters a statement binds. Each list
# is bounded independently because they never share a statement.
MAX_PURGE_IDS = 10_000

PurgeIdList = Annotated[list[EntityId], Field(max_length=MAX_PURGE_IDS)]


class ProjectTrashRead(ProjectRead):
    """A trashed project plus how many tasks would return if restored with it.

    ``archived_task_count`` is the set cascade-soft-deleted with the project (it
    drives the restore confirm). ``purge_task_count`` is the wider set a purge
    destroys — every trashed task the project still owns, including ones the user
    trashed independently — so the irreversible confirm can't understate its scope
    (BUG #189). Only used by the trash list, so the shared ``ProjectRead`` stays
    unchanged.
    """

    archived_task_count: int = 0
    purge_task_count: int = 0


class TrashRead(BaseModel):
    """Recently soft-deleted rows across the user-facing tables (Sprint 7).

    Powers the single /trash page — one fetch, restore via per-entity routes.
    """

    projects: list[ProjectTrashRead]
    tasks: list[TaskRead]


# --- Undo receipts (#306, #307) ---------------------------------------------
#
# A restore is not always the inverse of a plain delete: a task restore brings
# back one row (or a skipped occurrence's checklist, or rewinds a live successor
# and purges the skipped row), and a project restore may leave its tasks in the
# standalone trash. The Trash page's Undo therefore replays a *receipt* of what
# the restore actually did, handed back by the restore itself. The receipt comes
# back from the client, so these are mutation models: every field is validated
# here and the service re-checks it against the current rows before writing.


class RescheduledDate(MutationModel):
    """One row an un-skip moved onto the skipped date, and where it was before.

    ``None`` for a checklist row that had no due date until the rewind set one.
    """

    task_id: EntityId
    due_date: date | None


class UnskipUndo(MutationModel):
    """What an un-skip rewind changed: the date it rewound onto and the old dates.

    The restored skipped row itself was purged by the rewind, so undoing it
    re-files a skipped occurrence on ``skipped_due_date`` and moves each rewound
    row back to its ``previous_due_dates`` entry.
    """

    skipped_due_date: date
    previous_due_dates: Annotated[
        list[RescheduledDate], Field(max_length=MAX_PURGE_IDS)
    ]


class TaskRestoreUndo(MutationModel):
    """Receipt for one Trash task restore — ``POST /api/trash/tasks/undo-restore``.

    ``task_id`` is the row the restore handed back. For an ordinary restore,
    ``restored_task_ids`` names exactly the rows it brought out of the trash
    (``task_id`` first, then any checklist an in-place un-skip took with it),
    ``skipped`` says the root was a skipped occurrence, and
    ``deleted_with_task_id`` is the cascade marker the root carried before the
    restore cleared it. For an un-skip rewind ``unskip`` is set instead and
    ``restored_task_ids`` is empty: nothing came out of the trash.
    """

    task_id: EntityId
    restored_task_ids: Annotated[list[EntityId], Field(max_length=MAX_PURGE_IDS)] = []
    skipped: bool = False
    deleted_with_task_id: EntityId | None = None
    unskip: UnskipUndo | None = None


class TaskRestoreResult(BaseModel):
    """A Trash task restore: the task it handed back plus the receipt to undo it."""

    task: TaskRead
    undo: TaskRestoreUndo


class ProjectRestoreUndo(MutationModel):
    """Receipt for one project restore — ``POST /api/trash/projects/undo-restore``.

    ``restored_task_ids`` came back with the project (``restore_tasks``);
    ``unarchived_task_ids`` stayed in the trash but lost their cascade marker,
    moving into the standalone Tasks trash. Undo re-archives both under the
    project.
    """

    project_id: EntityId
    restored_task_ids: Annotated[list[EntityId], Field(max_length=MAX_PURGE_IDS)] = []
    unarchived_task_ids: Annotated[
        list[EntityId], Field(max_length=MAX_PURGE_IDS)
    ] = []


class ProjectRestoreResult(BaseModel):
    """Outcome of restoring a project — the project plus how many tasks came back."""

    project: ProjectRead
    restored_task_count: int


class ProjectRestoreRouteResult(ProjectRestoreResult):
    """The HTTP restore's result: plus the receipt the Trash page's Undo replays.

    A subclass so the agent tool's ``restore_project`` result (the shared base)
    doesn't grow a field the model has no use for.
    """

    undo: ProjectRestoreUndo


class PurgeSelectedRequest(MutationModel):
    """Which trashed rows ``POST /api/trash/purge`` should permanently remove.

    Ids not in trash are skipped rather than rejected: purging a parent task takes
    its subtree with it, so a child selected alongside its parent is legitimately
    gone by the time the server reaches it (BUG-11).

    Each list is capped at ``MAX_PURGE_IDS``; a longer one is a 422 rather than
    the ``too many SQL variables`` 500 it used to become (#264).
    """

    project_ids: PurgeIdList = []
    task_ids: PurgeIdList = []


class EmptyTrashResult(BaseModel):
    """Per-kind counts of rows permanently removed by ``DELETE /api/trash`` (9f).

    Also the response for ``POST /api/trash/purge`` — same shape, same meaning.
    """

    projects: int
    tasks: int


class TrashCountResult(BaseModel):
    """Exact trash counts for the nav badge and the Empty-trash confirm (9f).

    ``projects`` / ``tasks`` drive the badge and section headings (soft-deleted
    projects; *standalone* soft-deleted tasks). ``purge_total`` is the exact
    number of rows ``DELETE /api/trash`` would remove — including tasks archived
    with deleted projects and excluding protected projects — so the Empty-trash
    confirm names a figure that matches the actual deletion.
    """

    projects: int
    tasks: int
    purge_total: int
