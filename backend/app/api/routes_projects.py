from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.guards import trashed_row_or_error
from app.db.models import ActivityEvent, Project, ProjectAlias
from app.db.session import get_db
from app.schemas.activity import ActivityEventRead
from app.schemas.projects import (
    ProjectAliasCreate,
    ProjectAliasRead,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)
from app.schemas.trash import ProjectRestoreResult
from app.services import activity as activity_service
from app.services import projects as projects_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_or_404(db: Session, project_id: int) -> Project:
    project = projects_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return project


def _get_alias_or_404(db: Session, project_id: int, alias_id: int) -> ProjectAlias:
    alias = projects_service.get_alias(db, alias_id)
    if alias is None or alias.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alias not found"
        )
    return alias


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> Sequence[Project]:
    return projects_service.list_projects(db)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, db: Session = Depends(get_db)) -> Project:
    return _get_or_404(db, project_id)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(data: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    project = projects_service.create_project(
        db, name=data.name, description=data.description
    )
    db.commit()
    db.refresh(project)
    logger.info("project_created", project_id=project.id)
    return project


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: int, data: ProjectUpdate, db: Session = Depends(get_db)
) -> Project:
    project = _get_or_404(db, project_id)
    updated = projects_service.update_project(
        db, project, data.model_dump(exclude_unset=True)
    )
    db.commit()
    db.refresh(updated)
    logger.info("project_updated", project_id=updated.id)
    return updated


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db)) -> None:
    project = _get_or_404(db, project_id)
    try:
        projects_service.soft_delete_project(db, project)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    logger.info("project_deleted", project_id=project_id)


@router.post("/{project_id}/restore", response_model=ProjectRestoreResult)
def restore_project(
    project_id: int,
    restore_tasks: bool = False,
    db: Session = Depends(get_db),
) -> ProjectRestoreResult:
    project = projects_service.get_deleted_project(db, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No deleted project with that id",
        )
    restored, restored_task_count = projects_service.restore_project(
        db, project, restore_tasks=restore_tasks
    )
    db.commit()
    db.refresh(restored)
    logger.info(
        "project_restored",
        project_id=restored.id,
        restored_task_count=restored_task_count,
    )
    return ProjectRestoreResult(
        project=ProjectRead.model_validate(restored),
        restored_task_count=restored_task_count,
    )


@router.delete(
    "/{project_id}/purge",
    status_code=status.HTTP_204_NO_CONTENT,
)
def purge_project(project_id: int, db: Session = Depends(get_db)) -> None:
    project = trashed_row_or_error(
        projects_service.get_deleted_project(db, project_id),
        lambda: projects_service.get_project(db, project_id),
        conflict_detail="Project is not in trash; delete it first",
        absent_detail="No deleted project with that id",
    )
    try:
        projects_service.purge_project(db, project)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    db.commit()
    logger.info("project_purged", project_id=project_id)


@router.get("/{project_id}/activity", response_model=list[ActivityEventRead])
def list_activity(
    project_id: int, limit: int = 50, db: Session = Depends(get_db)
) -> Sequence[ActivityEvent]:
    _get_or_404(db, project_id)
    return activity_service.list_events(db, project_id, limit=limit)


@router.get("/{project_id}/aliases", response_model=list[ProjectAliasRead])
def list_aliases(
    project_id: int, db: Session = Depends(get_db)
) -> Sequence[ProjectAlias]:
    _get_or_404(db, project_id)
    return projects_service.list_aliases(db, project_id)


@router.post(
    "/{project_id}/aliases",
    response_model=ProjectAliasRead,
    status_code=status.HTTP_201_CREATED,
)
def create_alias(
    project_id: int, data: ProjectAliasCreate, db: Session = Depends(get_db)
) -> ProjectAlias:
    _get_or_404(db, project_id)
    try:
        alias = projects_service.create_alias(
            db, project_id=project_id, alias=data.alias
        )
    except projects_service.DuplicateAliasError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(alias)
    logger.info("project_alias_created", project_id=project_id, alias_id=alias.id)
    return alias


@router.delete(
    "/{project_id}/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_alias(
    project_id: int, alias_id: int, db: Session = Depends(get_db)
) -> None:
    alias = _get_alias_or_404(db, project_id, alias_id)
    projects_service.soft_delete_alias(db, alias)
    db.commit()
    logger.info("project_alias_deleted", project_id=project_id, alias_id=alias_id)
