"""What the in-app model reads back from a tool call: a trimmed view (#414).

Tool bodies answer with the REST read models, and those are what MCP clients,
the persisted run record and the UI's undo all see. The loop's local model
pays for every character in prefill for the rest of the run, so before a
result becomes a ``role: tool`` message it is projected here: a task is its
id, title, project *name*, status, priority, due date and the flags that are
true; a project is its id, name and open-task count; a task list says how many
match and whether there is more. Nulls and false flags are left out.

Results with no view here (strings, focus, dependencies, search, activity)
pass through unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Project
from app.schemas.projects import ProjectRead
from app.schemas.tasks import TaskPage, TaskRead
from app.schemas.trash import ProjectRestoreResult, ProjectTrashRead, TrashRead
from app.services import dashboard
from app.tools import runtime


def agent_view(outcome: Any) -> Any:
    """``outcome`` as the model reads it; unchanged when it has no view."""
    if isinstance(outcome, TaskRead):
        with runtime.session_factory() as db:
            return task_detail(outcome, project_names(db, [outcome.project_id]))
    if isinstance(outcome, TaskPage):
        with runtime.session_factory() as db:
            names = project_names(db, (t.project_id for t in outcome.items))
        return {
            "tasks": [task_row(t, names) for t in outcome.items],
            "total": outcome.total,
            "has_more": outcome.has_more,
        }
    if isinstance(outcome, ProjectRead):
        with runtime.session_factory() as db:
            return project_view(outcome, dashboard.open_task_counts(db), detail=True)
    if isinstance(outcome, list) and outcome and all(
        isinstance(item, ProjectRead) for item in outcome
    ):
        with runtime.session_factory() as db:
            counts = dashboard.open_task_counts(db)
        return [project_view(p, counts) for p in outcome]
    if isinstance(outcome, ProjectRestoreResult):
        with runtime.session_factory() as db:
            counts = dashboard.open_task_counts(db)
        return {
            "project": project_view(outcome.project, counts, detail=True),
            "restored_task_count": outcome.restored_task_count,
        }
    if isinstance(outcome, TrashRead):
        with runtime.session_factory() as db:
            names = project_names(db, (t.project_id for t in outcome.tasks))
        return {
            "projects": [trashed_project_view(p) for p in outcome.projects],
            "tasks": [task_row(t, names) for t in outcome.tasks],
        }
    return outcome


def task_row(task: TaskRead, project_names: Mapping[int, str]) -> dict[str, Any]:
    """A task as one list row: who it is, where, its state, and true flags only."""
    row: dict[str, Any] = {
        "id": task.id,
        "title": task.title,
        "project": project_names.get(task.project_id, f"#{task.project_id}"),
        "status": task.workflow_status.value,
        "priority": task.priority.value,
    }
    if task.due_date is not None:
        row["due"] = task.due_date.isoformat()
    if task.parent_task_id is not None:
        row["parent"] = task.parent_task_id
    if task.has_subtasks:
        row["subtasks"] = True
    if task.repeat_interval is not None:
        row["recurring"] = True
    if task.is_blocked:
        row["blocked"] = True
    if task.is_blocking:
        row["blocking"] = True
    return row


def task_detail(task: TaskRead, project_names: Mapping[int, str]) -> dict[str, Any]:
    """A single task (a lookup or a write's answer): the row plus the set fields
    a write can change, so the answer shows what was changed."""
    view = task_row(task, project_names)
    view.pop("recurring", None)
    optional: dict[str, Any] = {
        "description": task.description,
        "estimated_minutes": task.estimated_minutes,
        "actual_minutes": task.actual_minutes,
        "deferred_until": task.deferred_until and task.deferred_until.isoformat(),
        "repeat": task.repeat_interval and task.repeat_interval.model_dump(),
        "next_occurrence": task.next_occurrence_date and task.next_occurrence_date.isoformat(),
    }
    view.update({key: value for key, value in optional.items() if value is not None})
    return view


def project_view(
    project: ProjectRead, open_counts: Mapping[int, int], *, detail: bool = False
) -> dict[str, Any]:
    """A project as its id, name and open-task count (plus closed, and in a
    single-project answer its description, when set)."""
    view: dict[str, Any] = {
        "id": project.id,
        "name": project.name,
        "open_tasks": open_counts.get(project.id, 0),
    }
    if project.closed_at is not None:
        view["closed"] = True
    if detail and project.description:
        view["description"] = project.description
    return view


def trashed_project_view(project: ProjectTrashRead) -> dict[str, Any]:
    """A trashed project: id, name and how many tasks a restore brings back."""
    return {
        "id": project.id,
        "name": project.name,
        "tasks_trashed_with_it": project.archived_task_count,
    }


def project_names(db: Session, project_ids: Iterable[int]) -> dict[int, str]:
    """Names by id, trashed projects included (a trashed task can point at one)."""
    ids = set(project_ids)
    if not ids:
        return {}
    rows = db.execute(select(Project.id, Project.name).where(Project.id.in_(ids)))
    return {project_id: name for project_id, name in rows.tuples()}
