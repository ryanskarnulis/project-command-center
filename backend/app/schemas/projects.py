from __future__ import annotations


from pydantic import BaseModel, ConfigDict

from app.schemas.common import NonBlankStr, OptionalStrippedStr, UTCDateTime


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
    created_at: UTCDateTime
    updated_at: UTCDateTime
    deleted_at: UTCDateTime | None = None
