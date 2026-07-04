from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import ActivityEvent, InboxItem, Project, ProjectAlias, Task
from app.services import activity
from app.services.common import active, deleted, hard_delete, restore, soft_delete

class DuplicateAliasError(ValueError):
    """Raised when an alias whose normalized form already exists is added."""


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


def _mark_subtree_deleted_with_project(
    db: Session, task: Task, project_id: int
) -> None:
    """Stamp ``task`` and its whole active subtree with the owning project id.

    Stamped before the cascade soft-delete so ``restore_project`` can bring back
    exactly the set that the project deletion removed (and ``list_deleted_tasks``
    can hide it from the standalone Tasks trash section).
    """
    from app.services import tasks as tasks_service

    task.deleted_with_project_id = project_id
    for child in tasks_service.list_subtasks(db, task.id):
        _mark_subtree_deleted_with_project(db, child, project_id)


def soft_delete_project(db: Session, project: Project) -> None:
    if project.is_protected:
        raise ValueError(f'Project "{project.name}" is protected and cannot be deleted')

    # Cascade: a deleted project takes its tasks (and their subtrees) into the
    # trash with it, instead of rehoming them to General. Restore offers to bring
    # them back (see restore_project). Tasks already trashed independently keep
    # their null marker and are left untouched.
    from app.services import tasks as tasks_service

    top_level = db.execute(
        active(Task).where(
            Task.project_id == project.id, Task.parent_task_id.is_(None)
        )
    ).scalars().all()
    for task in top_level:
        _mark_subtree_deleted_with_project(db, task, project.id)
        tasks_service.soft_delete_task(db, task)

    # Safety sweep: any task still active in this project (e.g. a subtask whose
    # parent lives in a different project, so the cascade above never reached it).
    for task in db.execute(
        active(Task).where(Task.project_id == project.id)
    ).scalars().all():
        task.deleted_with_project_id = project.id
        tasks_service.soft_delete_task(db, task)

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


def count_tasks_deleted_with_project(db: Session, project_id: int) -> int:
    """How many trashed tasks would come back if this project is restored with them."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.deleted_at.is_not(None),
                Task.deleted_with_project_id == project_id,
            )
        )
        or 0
    )


def restore_project(
    db: Session, project: Project, *, restore_tasks: bool = False
) -> tuple[Project, int]:
    """Restore a trashed project; optionally bring back its cascade-deleted tasks.

    Returns ``(project, restored_task_count)``. Only tasks stamped with this
    project's id at delete time are pulled back — tasks the user trashed
    independently keep their null marker and stay in the trash.
    """
    restore(project)

    restored_tasks = 0
    if restore_tasks:
        tasks = db.execute(
            deleted(Task).where(Task.deleted_with_project_id == project.id)
        ).scalars().all()
        for task in tasks:
            restore(task)
            task.deleted_with_project_id = None
            restored_tasks += 1

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
    return project, restored_tasks


# --- Permanent delete / purge (Sprint 9f) ----------------------------------


def purge_project(db: Session, project: Project) -> None:
    """Permanently delete a trashed project and clean every FK edge into it.

    Protected (``General``) is never purgeable. A deleted project's tasks are
    cascade-soft-deleted with it (they keep ``project_id``), so every task still
    pointing here is purged (via ``purge_task`` so their dependency/subtree edges
    go too). Aliases are hard-deleted. The nullable FKs that would otherwise
    dangle — ``inbox_items.suggested_project_id``, ``activity_events.project_id``
    (the audit log, kept but with the ref cleared), and any
    ``tasks.deleted_with_project_id`` still pointing here — are nulled.
    ``hard_delete``'s guard enforces the project is already in trash. Caller commits.
    """
    from app.services import task_trash  # local: avoid circular import

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
            task_trash.purge_task(db, task)

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
    db.execute(
        update(Task)
        .where(Task.deleted_with_project_id == project.id)
        .values(deleted_with_project_id=None)
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


def find_active_alias_by_normalized(
    db: Session, project_id: int, normalized: str
) -> ProjectAlias | None:
    return db.execute(
        active(ProjectAlias).where(
            ProjectAlias.project_id == project_id,
            ProjectAlias.normalized_alias == normalized,
        )
    ).scalar_one_or_none()


def create_alias(db: Session, *, project_id: int, alias: str) -> ProjectAlias:
    normalized = _normalize(alias)
    if find_active_alias_by_normalized(db, project_id, normalized) is not None:
        raise DuplicateAliasError(f'Alias "{alias}" already exists for this project')
    row = ProjectAlias(project_id=project_id, alias=alias, normalized_alias=normalized)
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


class ProjectMatch(NamedTuple):
    """A deterministic project match plus *how* it matched.

    ``matched_alias`` is the raw alias string when the note matched one of the
    project's aliases, and ``None`` when it matched the project's own name — so a
    caller can honestly tell the user "matched alias 'firewall'" without claiming
    an alias for a plain name hit.
    """

    project: Project
    matched_alias: str | None


def match_text_to_project_detailed(db: Session, text: str | None) -> ProjectMatch | None:
    """Like :func:`match_text_to_project`, but also reports the matched alias.

    A project matches when its normalized name, or any of its normalized aliases,
    appears as a substring of the normalized ``text`` — which the caller builds
    from everything the note offers (the model's ``project_hint``, the summary,
    the raw text, and the task titles). Searching the raw text, not just the
    hint, is the point: the extractor often won't surface an alias as the hint,
    but the alias is right there in the note ("finish the *firewall* cleanup…").

    A name match takes precedence over an alias match (``matched_alias`` is then
    ``None``). Returns a match only when exactly one project matches — zero or an
    ambiguous (multi-project) result returns ``None`` so the caller can fall back
    to the AI matcher. Pure Python: no model is consulted here.
    """
    if text is None:
        return None
    norm = _normalize(text)
    if not norm:
        return None

    matches: list[ProjectMatch] = []
    for project, aliases in list_projects_with_aliases(db):
        name_norm = _normalize(project.name)
        if name_norm and name_norm in norm:
            matches.append(ProjectMatch(project, None))
            continue
        matched_alias = next(
            (
                alias
                for alias in aliases
                if (alias_norm := _normalize(alias)) and alias_norm in norm
            ),
            None,
        )
        if matched_alias is not None:
            matches.append(ProjectMatch(project, matched_alias))
    return matches[0] if len(matches) == 1 else None


def match_text_to_project(db: Session, text: str | None) -> Project | None:
    """Deterministically resolve a project from a note's text.

    Thin wrapper over :func:`match_text_to_project_detailed` for callers that only
    need the project. See that function for the matching rules.
    """
    match = match_text_to_project_detailed(db, text)
    return match.project if match is not None else None


def find_project_by_name_or_alias(db: Session, text: str) -> Project | None:
    """Exactly resolve a project by its (normalized) name or one of its aliases.

    Unlike ``match_text_to_project`` (intentionally fuzzy substring matching used
    by inbox triage), this is an *exact* normalized-equality lookup: the Discord
    ``/tasks <project>`` filter should only match a project the user named, not
    every project whose name happens to appear inside the query. Returns the
    single match, or ``None`` when the name/alias is unknown.
    """
    norm = _normalize(text)
    if not norm:
        return None
    for project, aliases in list_projects_with_aliases(db):
        if _normalize(project.name) == norm or any(
            _normalize(alias) == norm for alias in aliases
        ):
            return project
    return None
