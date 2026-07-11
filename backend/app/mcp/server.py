"""PCC MCP server (stdio): task/project CRUD, search, focus, trash/restore.

Run with ``python -m app.mcp.server`` from ``backend/`` (the repo's
``.mcp.json`` does exactly that). Each tool is a thin adapter: validate
arguments (Pydantic, reusing the API schemas), open a session, call the
service layer, serialize with the API read models. No business logic here.

Deliberately not exposed: ``purge_task``, ``purge_project``, ``empty_trash``
(hard deletes are structurally unreachable), ``reorder_projects`` (pure UI),
and the dashboard overview (composable from ``list_projects`` + ``list_tasks``).
"""

from __future__ import annotations

import sys
from datetime import date as date_type
from typing import Annotated

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.task_reads import read_with_blocked, reads_with_blocked
from app.db.models import Project, Task, TaskWorkflowStatus
from app.logging_config import configure_logging
from app.mcp.runtime import tool_session
from app.schemas.activity import ActivityEventRead
from app.schemas.focus import FocusPlan
from app.schemas.projects import ProjectCreate, ProjectRead, ProjectUpdate
from app.schemas.search import SearchResults
from app.schemas.tasks import TaskCreate, TaskRead, TaskUpdate
from app.schemas.trash import ProjectRestoreResult, ProjectTrashRead, TrashRead
from app.services import activity as activity_service
from app.services import focus as focus_service
from app.services import projects as projects_service
from app.services import search as search_service
from app.services import task_trash
from app.services import tasks as tasks_service

logger = structlog.get_logger(__name__)

mcp = FastMCP(
    "pcc",
    instructions=(
        "Project Command Center (PCC): local project and task management. "
        "All deletes are soft deletes into a trash (restorable via the "
        "restore_* tools); there is no permanent delete. Every write is "
        "recorded in the activity log attributed to this agent."
    ),
)

# Read caps: the agent never needs an unbounded scan; a genuinely bigger
# window is expressed explicitly via offset paging.
_ListLimit = Annotated[int, Field(ge=1, le=200)]
_PerKind = Annotated[int, Field(ge=1, le=25)]
_StartTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
_AvailableMinutes = Annotated[int, Field(ge=15, le=1440)]

# Domain rejections the service layer raises on bad-but-well-typed input;
# surfaced as tool errors so the model can read the reason and self-correct.
_TASK_DOMAIN_ERRORS = (
    tasks_service.TaskCycleError,
    tasks_service.DerivedStatusError,
    tasks_service.BlockedTaskError,
    tasks_service.RecurrenceError,
)


def _task_or_error(db: Session, task_id: int) -> Task:
    task = tasks_service.get_task(db, task_id)
    if task is None:
        raise ToolError(f"Task {task_id} not found")
    return task


def _project_or_error(db: Session, project_id: int) -> Project:
    project = projects_service.get_project(db, project_id)
    if project is None:
        raise ToolError(f"Project {project_id} not found")
    return project


def _ensure_project_exists(db: Session, project_id: int) -> None:
    _project_or_error(db, project_id)


# --- Tasks -------------------------------------------------------------------


@mcp.tool()
def list_tasks(
    project_id: int | None = None,
    workflow_status: TaskWorkflowStatus | None = None,
    exclude_done: bool = False,
    top_level_only: bool = False,
    limit: _ListLimit = 50,
    offset: int = 0,
) -> list[TaskRead]:
    """List active tasks, newest first. Omit project_id to search all projects."""
    with tool_session("list_tasks") as db:
        if project_id is not None:
            _ensure_project_exists(db, project_id)
        tasks = tasks_service.list_tasks(
            db,
            project_id,
            workflow_status=workflow_status,
            exclude_done=exclude_done,
            top_level_only=top_level_only,
            limit=limit,
            offset=offset,
        )
        return reads_with_blocked(db, tasks)


@mcp.tool()
def get_task(task_id: int) -> TaskRead:
    """Fetch one task with its subtask roll-up and blocked/blocking flags."""
    with tool_session("get_task") as db:
        return read_with_blocked(db, _task_or_error(db, task_id))


@mcp.tool()
def create_task(data: TaskCreate) -> TaskRead:
    """Create a task. Omit project_id to file it in the General project."""
    with tool_session("create_task") as db:
        if data.project_id is not None:
            _ensure_project_exists(db, data.project_id)
        try:
            task = tasks_service.create_task(
                db,
                project_id=data.project_id,
                title=data.title,
                description=data.description,
                workflow_status=data.workflow_status,
                priority=data.priority,
                due_date=data.due_date,
                parent_task_id=data.parent_task_id,
                estimated_minutes=data.estimated_minutes,
            )
        except _TASK_DOMAIN_ERRORS as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("mcp_task_created", task_id=task.id, project_id=task.project_id)
        return read_with_blocked(db, task)


@mcp.tool()
def update_task(task_id: int, changes: TaskUpdate) -> TaskRead:
    """Partially update a task; only the fields present in `changes` are touched.

    A parent's workflow_status is derived from its subtasks and cannot be set
    directly; a blocked task cannot be marked done. Recurring tasks accept
    edit_scope "this" (default) or "future".
    """
    with tool_session("update_task") as db:
        task = _task_or_error(db, task_id)
        fields = changes.model_dump(exclude_unset=True)
        if fields.get("project_id") is not None:
            _ensure_project_exists(db, fields["project_id"])
        try:
            updated = tasks_service.update_task(db, task, fields)
        except _TASK_DOMAIN_ERRORS as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("mcp_task_updated", task_id=updated.id)
        return read_with_blocked(db, updated)


@mcp.tool()
def complete_task(task_id: int) -> TaskRead:
    """Mark a task done. A recurring task spawns its next occurrence."""
    with tool_session("complete_task") as db:
        task = _task_or_error(db, task_id)
        try:
            updated = tasks_service.mark_done(db, task)
        except _TASK_DOMAIN_ERRORS as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("mcp_task_completed", task_id=updated.id)
        return read_with_blocked(db, updated)


@mcp.tool()
def reopen_task(task_id: int) -> TaskRead:
    """Reopen a done task (workflow_status back to open)."""
    with tool_session("reopen_task") as db:
        task = _task_or_error(db, task_id)
        try:
            updated = tasks_service.reopen_task(db, task)
        except _TASK_DOMAIN_ERRORS as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("mcp_task_reopened", task_id=updated.id)
        return read_with_blocked(db, updated)


@mcp.tool()
def trash_task(task_id: int) -> str:
    """Move a task (and its subtasks) to the trash. Undo with restore_task."""
    with tool_session("trash_task") as db:
        task = _task_or_error(db, task_id)
        title = task.title
        tasks_service.soft_delete_task(db, task)
        logger.info("mcp_task_trashed", task_id=task_id)
        return f'Task {task_id} "{title}" moved to trash (undo with restore_task)'


@mcp.tool()
def restore_task(task_id: int) -> TaskRead:
    """Restore a trashed task (see list_trash for what is restorable)."""
    with tool_session("restore_task") as db:
        task = task_trash.get_deleted_task(db, task_id)
        if task is None:
            raise ToolError(f"No deleted task with id {task_id}")
        restored = task_trash.restore_task(db, task)
        db.flush()
        logger.info("mcp_task_restored", task_id=restored.id)
        return read_with_blocked(db, restored)


# --- Projects ----------------------------------------------------------------


@mcp.tool()
def list_projects(include_closed: bool = False) -> list[ProjectRead]:
    """List active projects in display order."""
    with tool_session("list_projects") as db:
        projects = projects_service.list_projects(db, include_closed=include_closed)
        return [ProjectRead.model_validate(p) for p in projects]


@mcp.tool()
def get_project(project_id: int) -> ProjectRead:
    """Fetch one project."""
    with tool_session("get_project") as db:
        return ProjectRead.model_validate(_project_or_error(db, project_id))


@mcp.tool()
def create_project(data: ProjectCreate) -> ProjectRead:
    """Create a project."""
    with tool_session("create_project") as db:
        project = projects_service.create_project(
            db, name=data.name, description=data.description
        )
        db.flush()
        logger.info("mcp_project_created", project_id=project.id)
        return ProjectRead.model_validate(project)


@mcp.tool()
def update_project(project_id: int, changes: ProjectUpdate) -> ProjectRead:
    """Rename a project or edit its description; only fields present are touched."""
    with tool_session("update_project") as db:
        project = _project_or_error(db, project_id)
        updated = projects_service.update_project(
            db, project, changes.model_dump(exclude_unset=True)
        )
        db.flush()
        logger.info("mcp_project_updated", project_id=updated.id)
        return ProjectRead.model_validate(updated)


@mcp.tool()
def close_project(project_id: int) -> ProjectRead:
    """Close a project: hidden from default lists, nothing deleted."""
    with tool_session("close_project") as db:
        project = _project_or_error(db, project_id)
        try:
            closed = projects_service.close_project(db, project)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("mcp_project_closed", project_id=closed.id)
        return ProjectRead.model_validate(closed)


@mcp.tool()
def reopen_project(project_id: int) -> ProjectRead:
    """Reopen a closed project."""
    with tool_session("reopen_project") as db:
        project = _project_or_error(db, project_id)
        reopened = projects_service.reopen_project(db, project)
        db.flush()
        logger.info("mcp_project_reopened", project_id=reopened.id)
        return ProjectRead.model_validate(reopened)


@mcp.tool()
def trash_project(project_id: int) -> str:
    """Move a project and its tasks to the trash. Undo with restore_project."""
    with tool_session("trash_project") as db:
        project = _project_or_error(db, project_id)
        name = project.name
        try:
            projects_service.soft_delete_project(db, project)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        logger.info("mcp_project_trashed", project_id=project_id)
        return (
            f'Project {project_id} "{name}" and its tasks moved to trash '
            "(undo with restore_project)"
        )


@mcp.tool()
def restore_project(
    project_id: int, restore_tasks: bool = False
) -> ProjectRestoreResult:
    """Restore a trashed project; restore_tasks also brings back the tasks trashed with it."""
    with tool_session("restore_project") as db:
        project = projects_service.get_deleted_project(db, project_id)
        if project is None:
            raise ToolError(f"No deleted project with id {project_id}")
        restored, restored_task_count = projects_service.restore_project(
            db, project, restore_tasks=restore_tasks
        )
        db.flush()
        logger.info(
            "mcp_project_restored",
            project_id=restored.id,
            restored_task_count=restored_task_count,
        )
        return ProjectRestoreResult(
            project=ProjectRead.model_validate(restored),
            restored_task_count=restored_task_count,
        )


# --- Search, focus, trash, activity ------------------------------------------


@mcp.tool()
def search(query: str, per_kind: _PerKind = 8) -> SearchResults:
    """Full-text search over active projects and tasks (title, description)."""
    with tool_session("search") as db:
        return search_service.search(db, query, per_kind=per_kind)


@mcp.tool()
def get_focus_plan(
    date: date_type | None = None,
    start_time: _StartTime = focus_service.DEFAULT_START_TIME,
    available_minutes: _AvailableMinutes = focus_service.DEFAULT_AVAILABLE_MINUTES,
) -> FocusPlan:
    """Deterministic day plan: what to work on for `date` (default: today)."""
    with tool_session("get_focus_plan") as db:
        return focus_service.get_focus_plan(
            db,
            target_date=date or date_type.today(),
            start_time=start_time,
            available_minutes=available_minutes,
        )


@mcp.tool()
def list_trash(limit: _ListLimit = 50) -> TrashRead:
    """Recently trashed projects and tasks — what restore_task/restore_project can bring back."""
    with tool_session("list_trash") as db:
        deleted_projects = projects_service.list_deleted_projects(db, limit=limit)
        return TrashRead(
            projects=[
                ProjectTrashRead.model_validate(p).model_copy(
                    update={
                        "archived_task_count": (
                            projects_service.count_tasks_deleted_with_project(db, p.id)
                        )
                    }
                )
                for p in deleted_projects
            ],
            tasks=[
                TaskRead.model_validate(t)
                for t in task_trash.list_deleted_tasks(db, limit=limit)
            ],
        )


@mcp.tool()
def list_activity(project_id: int, limit: _ListLimit = 50) -> list[ActivityEventRead]:
    """A project's audit trail, newest first. actor is null for the user, "agent:mcp" for this server."""
    with tool_session("list_activity") as db:
        _ensure_project_exists(db, project_id)
        events = activity_service.list_events(db, project_id, limit=limit)
        return [ActivityEventRead.model_validate(e) for e in events]


def main() -> None:
    # stdout carries the JSON-RPC transport; all logging must go to stderr.
    configure_logging(stream=sys.stderr)
    logger.info("mcp_server_starting", server="pcc")
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
