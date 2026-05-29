from __future__ import annotations

from collections.abc import Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import get_db
from app.schemas.projects import ProjectCreate, ProjectRead, ProjectUpdate
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
    logger.info("project_updated", project_id=updated.id)
    return updated


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db)) -> None:
    project = _get_or_404(db, project_id)
    projects_service.soft_delete_project(db, project)
    logger.info("project_deleted", project_id=project_id)
