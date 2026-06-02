from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, StringConstraints


def _blank_to_none(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    return stripped if stripped else None


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OptionalStrippedStr = Annotated[str | None, BeforeValidator(_blank_to_none)]
