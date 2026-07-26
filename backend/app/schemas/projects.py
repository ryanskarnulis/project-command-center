from __future__ import annotations


from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.common import (
    EntityId,
    MutationModel,
    NonBlankStr,
    OptionalStrippedStr,
    UTCDateTime,
)

# ``ProjectUpdate`` columns backed by NOT-NULL DB columns: an explicit ``null`` on
# any of these must be a 422, never a silent NOT-NULL violation.
_PROJECT_UPDATE_NON_NULLABLE_FIELDS = ("name",)


class ProjectCreate(MutationModel):
    name: NonBlankStr
    description: OptionalStrippedStr = None


class ProjectUpdate(MutationModel):
    name: NonBlankStr | None = None
    description: OptionalStrippedStr = None

    # The ``| None`` above is what lets a partial PATCH *omit* ``name`` via the
    # route's ``model_dump(exclude_unset=True)``, so it can't be dropped; instead
    # distinguish omit from explicit null via ``model_fields_set``. Mirrors
    # ``TaskUpdate._reject_null_non_nullable``. ``description`` is nullable in the
    # DB and may legitimately be cleared.
    @model_validator(mode="after")
    def _reject_null_non_nullable(self) -> "ProjectUpdate":
        for name in _PROJECT_UPDATE_NON_NULLABLE_FIELDS:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared to null")
        return self


class ProjectOrderUpdate(MutationModel):
    """Full manual order: every active project id, in display order."""

    project_ids: list[EntityId]


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sort_order: int
    name: str
    description: str | None
    system_key: str | None
    is_protected: bool
    created_at: UTCDateTime
    updated_at: UTCDateTime
    closed_at: UTCDateTime | None = None
    deleted_at: UTCDateTime | None = None
