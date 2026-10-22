"""Tool refusals that say how to recover (#413).

Every "no" a tool gives carries ``retry`` — whether calling again can possibly
help, decided here by code rather than inferred from the wording — and, where
a better call exists, the way to it: the open subtasks for a parent's derived
status, the blockers for a blocked task, the live occurrence holding a date,
where to find an id that was not found. Borrowed from chess's
``ToolRegistry.refusal`` (``chessapp/tools.py``).

- ``different_args`` — the same tool with other arguments can succeed (a typo'd
  id, a date already taken, a field that failed validation).
- ``never`` — repeating the call cannot help; the state has to change first,
  or the user has to be told.

Two readers, one refusal. The in-app loop feeds :meth:`ToolRefusal.payload`
back as JSON (``{"ok": false, "error", "retry", ...}``, built by the loop). MCP clients keep the
``ToolError`` text path: ``str()`` is the error plus one prose line built from
the same details, so they get the way forward without a new protocol shape.

Only reads happen here, in the failed tool's own session, after the service
layer refused; nothing is written.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.orm import Session

from app.db.models import Task, TaskWorkflowStatus
from app.services import task_dependencies as deps_service
from app.services import tasks as tasks_service

Retry = Literal["different_args", "never"]
RETRY_DIFFERENT_ARGS: Retry = "different_args"
RETRY_NEVER: Retry = "never"


class ToolRefusal(ToolError):
    """A tool's "no", with ``retry`` and whatever recovery needs.

    ``error`` is the bare reason; ``details`` the structured way forward (a
    ``next`` sentence, ids). ``str()`` joins the reason and a prose rendering of
    the details — the text an MCP client sees.
    """

    def __init__(self, error: str, retry: Retry, **details: Any) -> None:
        self.error = error
        self.retry = retry
        self.details = details
        hint = _prose(details)
        super().__init__(f"{error.rstrip('.')}. {hint}" if hint else error)


def not_found(error: str, next_step: str) -> ToolRefusal:
    """An id that names nothing: another id can succeed."""
    return ToolRefusal(error, RETRY_DIFFERENT_ARGS, next=next_step)


FIND_TASK = "Find the task's id with search or list_tasks"
FIND_PROJECT = "Find the project's id with list_projects or search"
FIND_TRASHED = "See list_trash for what can be restored"
FIND_FOCUS_ENTRY = "Find entry ids with get_focus_plan"
FIND_REMOVED_FOCUS_ENTRY = "Only an entry removed with remove_focus_entry can be restored"
FIND_DEPENDENCY = "Find dependency ids with list_dependencies"


def refusal_for(
    db: Session,
    exc: Exception,
    task: Task | None = None,
    *,
    args_fixable: bool = False,
) -> ToolRefusal:
    """The refusal for a service-layer rejection of a call on ``task``.

    ``args_fixable`` is the calling tool's word that a plain domain rejection
    of *its* arguments can be fixed by other ones (a create or edit: another
    parent, another date, a due date added); a rejection of the task's state
    (derived, blocked) is ``never`` whatever the tool.
    """
    error = str(exc)
    if isinstance(exc, tasks_service.DerivedStatusError) and task is not None:
        return _derived_status(db, error, task, exc.target)
    if isinstance(exc, tasks_service.DerivedEstimateError):
        return ToolRefusal(
            error,
            RETRY_NEVER,
            next="Set estimated_minutes on its subtasks; the parent sums them",
        )
    if isinstance(exc, tasks_service.BlockedTaskError) and task is not None:
        return _blocked(db, error, task)
    if isinstance(exc, tasks_service.OccurrenceConflictError):
        details: dict[str, Any] = {}
        conflict_id = getattr(exc, "conflict_id", None)
        conflict = None if conflict_id is None else db.get(Task, conflict_id)
        if conflict is not None:
            details["conflicting_occurrence"] = {
                "id": conflict.id,
                "title": conflict.title,
                "due_date": None if conflict.due_date is None else conflict.due_date.isoformat(),
            }
        return ToolRefusal(
            error, RETRY_DIFFERENT_ARGS if args_fixable else RETRY_NEVER, **details
        )
    return ToolRefusal(error, RETRY_DIFFERENT_ARGS if args_fixable else RETRY_NEVER)


def _derived_status(
    db: Session, error: str, task: Task, target: TaskWorkflowStatus
) -> ToolRefusal:
    subtasks = tasks_service.list_subtasks(db, task.id)
    statuses = deps_service.effective_statuses(db, [t.id for t in subtasks])
    if target == TaskWorkflowStatus.done:
        unfinished = [t for t in subtasks if statuses.get(t.id) != TaskWorkflowStatus.done]
        return ToolRefusal(
            error,
            RETRY_NEVER,
            open_subtasks=_task_refs(unfinished, statuses),
            next="Complete each open subtask with complete_task; the parent then reads done",
        )
    moved = [t for t in subtasks if statuses.get(t.id) != TaskWorkflowStatus.open]
    return ToolRefusal(
        error,
        RETRY_NEVER,
        subtasks=_task_refs(moved, statuses),
        next="Reopen these subtasks with reopen_task; the parent follows them",
    )


def _blocked(db: Session, error: str, task: Task) -> ToolRefusal:
    edges = deps_service.list_dependencies(db, task.id)
    statuses = deps_service.effective_statuses(db, [e.depends_on_task_id for e in edges])
    blocker_ids = [
        e.depends_on_task_id
        for e in edges
        if e.depends_on_task_id in statuses
        and statuses[e.depends_on_task_id] != TaskWorkflowStatus.done
    ]
    blockers = tasks_service.get_tasks(db, blocker_ids)
    return ToolRefusal(
        error,
        RETRY_NEVER,
        blockers=_task_refs([blockers[i] for i in blocker_ids if i in blockers], statuses),
        next="Finish the blockers first, or remove the dependency with remove_dependency",
    )


def _task_refs(
    tasks: Sequence[Task], statuses: dict[int, TaskWorkflowStatus]
) -> list[dict[str, Any]]:
    return [
        {
            "id": t.id,
            "title": t.title,
            "workflow_status": statuses.get(t.id, t.workflow_status).value,
        }
        for t in tasks
    ]


# The keys whose task lists the prose line names, and how it introduces them.
_LISTED = {
    "open_subtasks": "Open subtasks",
    "subtasks": "Subtasks",
    "blockers": "Blocked by",
}


def _prose(details: dict[str, Any]) -> str:
    """The details as one sentence or two, for a reader of text (MCP)."""
    parts: list[str] = []
    for key, label in _LISTED.items():
        refs = details.get(key)
        if refs:
            names = ", ".join(f'#{ref["id"]} "{ref["title"]}"' for ref in refs)
            parts.append(f"{label}: {names}.")
    conflict = details.get("conflicting_occurrence")
    if conflict:
        parts.append(f'The date is held by #{conflict["id"]} "{conflict["title"]}".')
    if details.get("next"):
        parts.append(f"{details['next']}.")
    return " ".join(parts)
