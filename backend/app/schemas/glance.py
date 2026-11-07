"""The glance (#504): one small read-only summary for conductor's ``look(app)``.

Shape per ``../agent-standard/app-yaml-glance-block.md``. Kept stable: conductor's
narrator speaks from it and its evals pin it.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.common import UTCDateTime


class GlanceTaskRef(BaseModel):
    """A task as a fleet entity ref (the delegate record's shape)."""

    app: str = "tasks"
    kind: str = "task"
    id: str
    title: str


class Glance(BaseModel):
    as_of: UTCDateTime
    due_today: list[GlanceTaskRef]
    # Set only when ``due_today`` was capped: how many more are due today.
    due_today_more: int | None = Field(default=None, exclude_if=lambda v: v is None)
    overdue: int
    focus: GlanceTaskRef | None
    next_due: date | None
