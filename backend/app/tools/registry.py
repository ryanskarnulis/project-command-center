"""Transport-agnostic tool registry: PCC's agent tool surface over the service layer.

The single source of truth for the agent tools — names, descriptions, argument
schemas, dispatch — consumed by two peers: the MCP server (``app/mcp/server.py``
registers every tool with FastMCP) and the in-app agent loop (``app/ai/loop.py``
advertises :func:`tool_specs` to the provider and dispatches via
:func:`call_tool`). Argument models and JSON Schemas come from the same
``func_metadata`` machinery FastMCP uses, so both consumers validate and
advertise exactly the same contract.

Each tool is a thin adapter: validate arguments (Pydantic, reusing the API
schemas), open a session, call the service layer, serialize with the API read
models. No business logic here.

Deliberately not registered: ``purge_task``, ``purge_project``, ``empty_trash``
(hard deletes are structurally unreachable from any agent), ``reorder_projects``
(pure UI), and the dashboard overview (composable from ``list_projects`` +
``list_tasks``).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_type
from typing import Annotated, Any, TypeVar

import structlog
from mcp.server.fastmcp.exceptions import ToolError as ToolError  # re-export
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata, func_metadata
from pydantic import Field
from sqlalchemy.orm import Session

from app.ai.providers.llamacpp import ToolSpec
from app.api.dependency_reads import dependency_read, dependent_read
from app.api.task_reads import read_with_blocked, reads_with_blocked
from app.db.models import Project, Task, TaskWorkflowStatus
from app.schemas.activity import ActivityEventRead
from app.schemas.focus import FocusPlan
from app.schemas.projects import ProjectCreate, ProjectRead, ProjectUpdate
from app.schemas.search import SearchResults
from app.schemas.task_dependencies import TaskDependenciesRead, TaskDependencyRead
from app.schemas.tasks import TaskCreate, TaskRead, TaskUpdate
from app.schemas.trash import ProjectRestoreResult, ProjectTrashRead, TrashRead
from app.services import activity as activity_service
from app.services import focus as focus_service
from app.services import projects as projects_service
from app.services import search as search_service
from app.services import task_dependencies as deps_service
from app.services import task_recurrence
from app.services import task_trash
from app.services import tasks as tasks_service
from app.tools import runtime
from app.tools.runtime import tool_session

logger = structlog.get_logger(__name__)


class UnknownToolError(Exception):
    """Dispatch was asked for a tool name the registry doesn't know."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown tool {name!r}")
        self.name = name


@dataclass(frozen=True)
class RegisteredTool:
    """One tool: callable body plus the metadata every consumer needs."""

    name: str
    description: str
    fn: Callable[..., Any]
    metadata: FuncMetadata

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for the arguments — identical to the MCP inputSchema."""
        return self.metadata.arg_model.model_json_schema(by_alias=True)


_REGISTRY: dict[str, RegisteredTool] = {}

_F = TypeVar("_F", bound=Callable[..., Any])


def _tool(fn: _F) -> _F:
    """Register a tool body: name from ``__name__``, description from the docstring."""
    description = inspect.getdoc(fn)
    if not description:
        raise ValueError(f"tool {fn.__name__} must have a docstring")
    _REGISTRY[fn.__name__] = RegisteredTool(
        name=fn.__name__,
        description=description,
        fn=fn,
        metadata=func_metadata(fn),
    )
    return fn


def all_tools() -> tuple[RegisteredTool, ...]:
    """Every registered tool, in registration order."""
    return tuple(_REGISTRY.values())


def get_tool(name: str) -> RegisteredTool:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownToolError(name) from None


def tool_specs() -> list[ToolSpec]:
    """The registry as provider tool specs, for ``LlamaCppProvider.chat(tools=…)``."""
    return [
        ToolSpec(name=tool.name, description=tool.description, parameters=tool.parameters)
        for tool in all_tools()
    ]


def call_tool(name: str, arguments: dict[str, Any], *, actor: str) -> Any:
    """Validate ``arguments`` against the tool's model, then run it as ``actor``.

    ``actor`` is stamped into ``activity_events`` for every write the call
    makes. Raises :class:`UnknownToolError` for an unregistered name,
    ``pydantic.ValidationError`` when the arguments fail the argument model
    (both schema-level — the caller can feed them back for self-correction),
    and :class:`ToolError` for not-found/domain rejections from the body.
    """
    tool = get_tool(name)
    validated = tool.metadata.arg_model.model_validate(tool.metadata.pre_parse_json(arguments))
    actor_token = runtime.current_tool_actor.set(actor)
    try:
        return tool.fn(**validated.model_dump_one_level())
    finally:
        runtime.current_tool_actor.reset(actor_token)


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


@_tool
def list_tasks(
    project_id: int | None = None,
    workflow_status: TaskWorkflowStatus | None = None,
    exclude_done: bool = False,
    top_level_only: bool = False,
    limit: _ListLimit = 50,
    offset: int = 0,
) -> list[TaskRead]:
    """List active tasks, oldest first. Omit project_id to search all projects."""
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


@_tool
def get_task(task_id: int) -> TaskRead:
    """Fetch one task with its subtask roll-up and blocked/blocking flags."""
    with tool_session("get_task") as db:
        return read_with_blocked(db, _task_or_error(db, task_id))


@_tool
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
        logger.info("tool_task_created", task_id=task.id, project_id=task.project_id)
        return read_with_blocked(db, task)


@_tool
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
        logger.info("tool_task_updated", task_id=updated.id)
        return read_with_blocked(db, updated)


@_tool
def complete_task(task_id: int) -> TaskRead:
    """Mark a task done. A recurring task spawns its next occurrence."""
    with tool_session("complete_task") as db:
        task = _task_or_error(db, task_id)
        try:
            updated = tasks_service.mark_done(db, task)
        except _TASK_DOMAIN_ERRORS as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("tool_task_completed", task_id=updated.id)
        return read_with_blocked(db, updated)


@_tool
def reopen_task(task_id: int) -> TaskRead:
    """Reopen a done task (workflow_status back to open)."""
    with tool_session("reopen_task") as db:
        task = _task_or_error(db, task_id)
        try:
            updated = tasks_service.reopen_task(db, task)
        except _TASK_DOMAIN_ERRORS as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("tool_task_reopened", task_id=updated.id)
        return read_with_blocked(db, updated)


@_tool
def trash_task(task_id: int) -> str:
    """Move a task (and its subtasks) to the trash. Undo with restore_task."""
    with tool_session("trash_task") as db:
        task = _task_or_error(db, task_id)
        title = task.title
        tasks_service.soft_delete_task(db, task)
        logger.info("tool_task_trashed", task_id=task_id)
        return f'Task {task_id} "{title}" moved to trash (undo with restore_task)'


@_tool
def restore_task(task_id: int) -> TaskRead:
    """Restore a trashed task (see list_trash for what is restorable)."""
    with tool_session("restore_task") as db:
        task = task_trash.get_deleted_task(db, task_id)
        if task is None:
            raise ToolError(f"No deleted task with id {task_id}")
        restored = task_trash.restore_task(db, task)
        db.flush()
        logger.info("tool_task_restored", task_id=restored.id)
        return read_with_blocked(db, restored)


# --- Dependencies and recurrence ----------------------------------------------


@_tool
def list_dependencies(task_id: int) -> TaskDependenciesRead:
    """Both directions of a task's dependency graph: what it waits on (depends_on) and what waits on it (dependents)."""
    with tool_session("list_dependencies") as db:
        _task_or_error(db, task_id)
        depends_on_edges = deps_service.list_dependencies(db, task_id)
        dependent_edges = deps_service.list_dependents(db, task_id)
        # One batched effective-status resolve per direction (no N+1 per edge).
        depends_on_effective = deps_service.effective_statuses(
            db, [e.depends_on_task_id for e in depends_on_edges]
        )
        dependent_effective = deps_service.effective_statuses(
            db, [e.task_id for e in dependent_edges]
        )
        return TaskDependenciesRead(
            depends_on=[
                dependency_read(db, e, depends_on_effective)
                for e in depends_on_edges
            ],
            dependents=[
                dependent_read(db, e, dependent_effective)
                for e in dependent_edges
            ],
        )


@_tool
def add_dependency(task_id: int, depends_on_task_id: int) -> TaskDependencyRead:
    """Make task_id wait on depends_on_task_id: it is blocked until that task is done."""
    with tool_session("add_dependency") as db:
        try:
            edge = deps_service.add_dependency(db, task_id, depends_on_task_id)
        except deps_service.DependencyError as exc:
            raise ToolError(str(exc)) from exc
        logger.info(
            "tool_dependency_added",
            task_id=task_id,
            depends_on_task_id=depends_on_task_id,
        )
        return dependency_read(db, edge)


@_tool
def remove_dependency(task_id: int, dependency_id: int) -> str:
    """Remove a dependency edge by its id (from list_dependencies); task_id must be its dependent."""
    with tool_session("remove_dependency") as db:
        edge = deps_service.get_dependency(db, dependency_id)
        if edge is None or edge.task_id != task_id:
            raise ToolError(f"Task {task_id} has no dependency {dependency_id}")
        deps_service.remove_dependency(db, edge)
        logger.info(
            "tool_dependency_removed", task_id=task_id, dependency_id=dependency_id
        )
        return f"Dependency {dependency_id} removed; task {task_id} no longer waits on task {edge.depends_on_task_id}"


@_tool
def skip_occurrence(task_id: int) -> TaskRead:
    """Skip a recurring task's current occurrence (to trash, restorable) and return the next one."""
    with tool_session("skip_occurrence") as db:
        task = _task_or_error(db, task_id)
        try:
            next_occurrence = task_recurrence.skip_occurrence(db, task)
        except tasks_service.RecurrenceError as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info(
            "tool_occurrence_skipped",
            task_id=task_id,
            next_task_id=next_occurrence.id,
        )
        return read_with_blocked(db, next_occurrence)


@_tool
def stop_recurrence(task_id: int) -> TaskRead:
    """Stop a recurring task from repeating; the current occurrence stays as a normal task."""
    with tool_session("stop_recurrence") as db:
        task = _task_or_error(db, task_id)
        try:
            updated = task_recurrence.stop_recurrence(db, task)
        except tasks_service.RecurrenceError as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("tool_recurrence_stopped", task_id=updated.id)
        return read_with_blocked(db, updated)


# --- Projects ----------------------------------------------------------------


@_tool
def list_projects(include_closed: bool = False) -> list[ProjectRead]:
    """List active projects in display order."""
    with tool_session("list_projects") as db:
        projects = projects_service.list_projects(db, include_closed=include_closed)
        return [ProjectRead.model_validate(p) for p in projects]


@_tool
def get_project(project_id: int) -> ProjectRead:
    """Fetch one project."""
    with tool_session("get_project") as db:
        return ProjectRead.model_validate(_project_or_error(db, project_id))


@_tool
def create_project(data: ProjectCreate) -> ProjectRead:
    """Create a project."""
    with tool_session("create_project") as db:
        project = projects_service.create_project(
            db, name=data.name, description=data.description
        )
        db.flush()
        logger.info("tool_project_created", project_id=project.id)
        return ProjectRead.model_validate(project)


@_tool
def update_project(project_id: int, changes: ProjectUpdate) -> ProjectRead:
    """Rename a project or edit its description; only fields present are touched."""
    with tool_session("update_project") as db:
        project = _project_or_error(db, project_id)
        updated = projects_service.update_project(
            db, project, changes.model_dump(exclude_unset=True)
        )
        db.flush()
        logger.info("tool_project_updated", project_id=updated.id)
        return ProjectRead.model_validate(updated)


@_tool
def close_project(project_id: int) -> ProjectRead:
    """Close a project: hidden from default lists, nothing deleted."""
    with tool_session("close_project") as db:
        project = _project_or_error(db, project_id)
        try:
            closed = projects_service.close_project(db, project)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        db.flush()
        logger.info("tool_project_closed", project_id=closed.id)
        return ProjectRead.model_validate(closed)


@_tool
def reopen_project(project_id: int) -> ProjectRead:
    """Reopen a closed project."""
    with tool_session("reopen_project") as db:
        project = _project_or_error(db, project_id)
        reopened = projects_service.reopen_project(db, project)
        db.flush()
        logger.info("tool_project_reopened", project_id=reopened.id)
        return ProjectRead.model_validate(reopened)


@_tool
def trash_project(project_id: int) -> str:
    """Move a project and its tasks to the trash. Undo with restore_project."""
    with tool_session("trash_project") as db:
        project = _project_or_error(db, project_id)
        name = project.name
        try:
            projects_service.soft_delete_project(db, project)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        logger.info("tool_project_trashed", project_id=project_id)
        return (
            f'Project {project_id} "{name}" and its tasks moved to trash '
            "(undo with restore_project)"
        )


@_tool
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
            "tool_project_restored",
            project_id=restored.id,
            restored_task_count=restored_task_count,
        )
        return ProjectRestoreResult(
            project=ProjectRead.model_validate(restored),
            restored_task_count=restored_task_count,
        )


# --- Search, focus, trash, activity ------------------------------------------


@_tool
def search(query: str, per_kind: _PerKind = 8) -> SearchResults:
    """Full-text search over active projects and tasks (title, description)."""
    with tool_session("search") as db:
        return search_service.search(db, query, per_kind=per_kind)


@_tool
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


@_tool
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


@_tool
def list_activity(project_id: int, limit: _ListLimit = 50) -> list[ActivityEventRead]:
    """A project's audit trail, newest first. actor is null for the user; agents stamp theirs (e.g. "agent:mcp", "agent:loop")."""
    with tool_session("list_activity") as db:
        _ensure_project_exists(db, project_id)
        events = activity_service.list_events(db, project_id, limit=limit)
        return [ActivityEventRead.model_validate(e) for e in events]
