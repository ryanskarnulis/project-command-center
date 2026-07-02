from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, StringConstraints

MAX_INBOX_RAW_TEXT_LENGTH = 8_000


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
