from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

MAX_INBOX_RAW_TEXT_LENGTH = 8_000

# SQLite stores INTEGER as a signed 64-bit value, so a larger Python int cannot
# be bound to an id column at all. A digit-only path segment above this used to
# parse fine as `int`, reach `Task.id == task_id`, and surface as a 500 instead
# of the 404 an unknown in-range id gets (#182). Bound ids at the boundary.
MAX_SQLITE_INT = 2**63 - 1


class MutationModel(BaseModel):
    """Base for every model that carries *input* into the service layer.

    Pydantic's default ``extra="ignore"`` silently drops unknown keys, so a typo
    (``prioirty``) or a stale client field validated cleanly, the route applied
    defaults or an empty field map, and the write path still reported success —
    a PATCH could log an ``updated`` activity event for a change that never
    happened (#164). ``extra="forbid"`` turns that into a structured 422 (or a
    Pydantic ``ValidationError`` the agent loop feeds back for self-correction)
    *before* the service layer is entered.

    Deliberately scoped to mutation inputs: read/response models keep the
    permissive default, so serializing an ORM row or accepting an extra field
    from an upstream provider payload is unaffected. This changes nothing about
    omit-vs-null PATCH semantics — routes still use
    ``model_dump(exclude_unset=True)``, and an omitted field stays omitted.
    """

    model_config = ConfigDict(extra="forbid")


def _blank_to_none(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    return stripped if stripped else None


def _assume_utc(value: datetime) -> datetime:
    """Stamp naive timestamps as UTC so serialized JSON always carries an offset.

    Every timestamp the app writes is UTC, but rows written before the aware
    Python-side defaults (or by SQLite's ``CURRENT_TIMESTAMP`` fallback) read
    back naive. Serializing those without an offset makes JS ``new Date(...)``
    parse them as *local* time, skewing displayed times by the UTC offset.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# The one type every DB-backed row id should use at the API boundary (path
# params and ID-bearing request payload fields alike): positive, and small
# enough for SQLite to bind. Out-of-range values fail validation as a 422 before
# any SQL runs.
EntityId = Annotated[int, Field(ge=1, le=MAX_SQLITE_INT)]

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# The one datetime type read schemas should use for DB-sourced timestamps.
UTCDateTime = Annotated[datetime, AfterValidator(_assume_utc)]
InboxRawText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_INBOX_RAW_TEXT_LENGTH,
    ),
]
OptionalStrippedStr = Annotated[str | None, BeforeValidator(_blank_to_none)]
