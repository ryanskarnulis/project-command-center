from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class ProjectAliasCreate(BaseModel):
    alias: str


class ProjectAliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    alias: str
    created_at: datetime
