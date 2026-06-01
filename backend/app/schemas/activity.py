from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ActivityEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    entity_type: str
    entity_id: int
    action: str
    summary: str
    created_at: datetime
