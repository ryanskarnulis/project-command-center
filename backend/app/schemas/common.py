from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, StringConstraints

MAX_INBOX_RAW_TEXT_LENGTH = 8_000


def _blank_to_none(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    return stripped if stripped else None


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
InboxRawText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_INBOX_RAW_TEXT_LENGTH,
    ),
]
OptionalStrippedStr = Annotated[str | None, BeforeValidator(_blank_to_none)]
