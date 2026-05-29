from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Project
from app.services.common import active, soft_delete


def list_projects(db: Session) -> Sequence[Project]:
    return db.execute(active(Project).order_by(Project.id)).scalars().all()


def get_project(db: Session, project_id: int) -> Project | None:
    return db.execute(
        active(Project).where(Project.id == project_id)
    ).scalar_one_or_none()


def create_project(db: Session, *, name: str, description: str | None = None) -> Project:
    project = Project(name=name, description=description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(db: Session, project: Project, fields: Mapping[str, Any]) -> Project:
    for key, value in fields.items():
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return project


def soft_delete_project(db: Session, project: Project) -> None:
    soft_delete(project)
    db.commit()
