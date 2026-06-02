from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import NonBlankStr, OptionalStrippedStr


class ProjectCreate(BaseModel):
    name: NonBlankStr
    description: OptionalStrippedStr = None


class ProjectUpdate(BaseModel):
    name: NonBlankStr | None = None
    description: OptionalStrippedStr = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    system_key: str | None
    is_protected: bool
    created_at: datetime
    updated_at: datetime


class ProjectAliasCreate(BaseModel):
    alias: NonBlankStr


class ProjectAliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    alias: str
    created_at: datetime
