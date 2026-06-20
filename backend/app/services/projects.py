from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, InboxItem, Project, ProjectAlias, Task
from app.services import activity
from app.services.common import active, deleted, hard_delete, restore, soft_delete

DEFAULT_PROJECT_NAME = "General"
DEFAULT_PROJECT_DESCRIPTION = "Default project for unfiled tasks"
DEFAULT_PROJECT_SYSTEM_KEY = "general"


def list_projects(db: Session) -> Sequence[Project]:
    return db.execute(active(Project).order_by(Project.id)).scalars().all()


def get_project(db: Session, project_id: int) -> Project | None:
    return db.execute(
        active(Project).where(Project.id == project_id)
    ).scalar_one_or_none()


def create_project(db: Session, *, name: str, description: str | None = None) -> Project:
    project = Project(name=name, description=description)
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
    project = get_default_project(db)
    if project is not None:
        return project

    project = db.execute(
        active(Project)
        .where(Project.name == DEFAULT_PROJECT_NAME, Project.system_key.is_(None))
        .order_by(Project.id)
    ).scalar_one_or_none()
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


def soft_delete_project(db: Session, project: Project) -> None:
    if project.is_protected:
        raise ValueError(f'Project "{project.name}" is protected and cannot be deleted')

    default_project = ensure_default_project(db)
    for task in db.execute(
        active(Task).where(Task.project_id == project.id)
    ).scalars():
        task.project_id = default_project.id

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


def restore_project(db: Session, project: Project) -> Project:
    restore(project)
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
    return project


# --- Permanent delete / purge (Sprint 9f) ----------------------------------


def purge_project(db: Session, project: Project) -> None:
    """Permanently delete a trashed project and clean every FK edge into it.

    Protected (``General``) is never purgeable. Active tasks were rehomed to
    General when the project was soft-deleted, so only soft-deleted tasks still
    point here — purge them (via ``purge_task`` so their dependency/subtree edges
    go too). Aliases are hard-deleted. The two nullable FKs that would otherwise
    dangle — ``inbox_items.suggested_project_id`` and ``activity_events.project_id``
    (the audit log, kept but with the ref cleared) — are nulled. ``hard_delete``'s
    guard enforces the project is already in trash. Caller commits.
    """
    from app.services import tasks as tasks_service  # local: avoid circular import

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
            tasks_service.purge_task(db, task)

    db.execute(sql_delete(ProjectAlias).where(ProjectAlias.project_id == project.id))
    db.execute(
        update(InboxItem)
        .where(InboxItem.suggested_project_id == project.id)
        .values(suggested_project_id=None)
    )
    db.execute(
        update(ActivityEvent)
        .where(ActivityEvent.project_id == project.id)
        .values(project_id=None)
    )

    hard_delete(db, project)


# --- Aliases & deterministic project matching (Sprint 4) -------------------


def _normalize(text: str) -> str:
    """Lowercase, trim, and collapse internal whitespace for matching."""
    return " ".join(text.split()).lower()


def list_aliases(db: Session, project_id: int) -> Sequence[ProjectAlias]:
    return (
        db.execute(
            active(ProjectAlias)
            .where(ProjectAlias.project_id == project_id)
            .order_by(ProjectAlias.id)
        )
        .scalars()
        .all()
    )


def get_alias(db: Session, alias_id: int) -> ProjectAlias | None:
    return db.execute(
        active(ProjectAlias).where(ProjectAlias.id == alias_id)
    ).scalar_one_or_none()


def create_alias(db: Session, *, project_id: int, alias: str) -> ProjectAlias:
    row = ProjectAlias(project_id=project_id, alias=alias)
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def soft_delete_alias(db: Session, alias: ProjectAlias) -> None:
    soft_delete(alias)
    db.flush()


def list_projects_with_aliases(
    db: Session,
) -> Sequence[tuple[Project, list[str]]]:
    """Active projects paired with their active alias strings.

    Feeds the AI project-matching fallback its choice list. Lives here (not in a
    workflow) so the service owns project data and stays free of any ``ai/``
    import.
    """
    aliases_by_project: dict[int, list[str]] = defaultdict(list)
    for row in db.execute(active(ProjectAlias).order_by(ProjectAlias.id)).scalars():
        aliases_by_project[row.project_id].append(row.alias)
    return [(project, aliases_by_project[project.id]) for project in list_projects(db)]


def match_text_to_project(db: Session, text: str | None) -> Project | None:
    """Deterministically resolve a project from a note's text.

    A project matches when its normalized name, or any of its normalized aliases,
    appears as a substring of the normalized ``text`` — which the caller builds
    from everything the note offers (the model's ``project_hint``, the summary,
    the raw text, and the task titles). Searching the raw text, not just the
    hint, is the point: the extractor often won't surface an alias as the hint,
    but the alias is right there in the note ("finish the *firewall* cleanup…").

    Returns the project only when exactly one matches — zero or an ambiguous
    (multi-project) result returns ``None`` so the caller can fall back to the AI
    matcher. Pure Python: no model is consulted here.
    """
    if text is None:
        return None
    norm = _normalize(text)
    if not norm:
        return None

    matches: list[Project] = []
    for project, aliases in list_projects_with_aliases(db):
        name_norm = _normalize(project.name)
        if (name_norm and name_norm in norm) or any(
            (alias_norm := _normalize(alias)) and alias_norm in norm for alias in aliases
        ):
            matches.append(project)
    return matches[0] if len(matches) == 1 else None
