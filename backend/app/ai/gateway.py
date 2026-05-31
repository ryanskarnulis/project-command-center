from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.ai.providers.base import BaseProvider, Message, ResponseMode
from app.ai.providers.ollama import OllamaProvider

_PROFILES_PATH = Path(__file__).parent / "profiles.yaml"
_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Provider registry. A profile's `provider` field selects one of these.
_PROVIDERS: dict[str, type[BaseProvider]] = {
    "ollama": OllamaProvider,
}


class Profile(BaseModel):
    """One entry from ``profiles.yaml``."""

    provider: str
    model: str
    temperature: float
    max_tokens: int
    response_mode: ResponseMode
    system_prompt: str


@lru_cache
def _load_profiles() -> dict[str, Profile]:
    raw: dict[str, Any] = yaml.safe_load(_PROFILES_PATH.read_text())
    return {name: Profile.model_validate(cfg) for name, cfg in raw.items()}


def get_profile(name: str) -> Profile:
    try:
        return _load_profiles()[name]
    except KeyError:
        raise ValueError(f"unknown profile: {name!r}") from None


def _load_prompt(filename: str) -> str:
    # Read fresh on every call (not cached): the settings UI will edit prompt
    # files at runtime and those edits must take effect without a restart.
    return (_PROMPTS_DIR / filename).read_text()


def complete(
    *,
    profile_name: str,
    user_content: str,
    json_schema: dict[str, Any] | None = None,
) -> str:
    """Run a single model call through the named profile and return raw text.

    This is the only entry point workflows use to reach a model. The gateway
    stays generic — it does not know about any particular output schema. The
    caller passes ``json_schema`` (e.g. ``ExtractionOutput.model_json_schema()``)
    and the gateway forwards it to the provider's structured-output support.
    """
    profile = get_profile(profile_name)
    try:
        provider_cls = _PROVIDERS[profile.provider]
    except KeyError:
        raise ValueError(f"unknown provider: {profile.provider!r}") from None

    messages: list[Message] = [
        {"role": "system", "content": _load_prompt(profile.system_prompt)},
        {"role": "user", "content": user_content},
    ]
    return provider_cls().complete(
        messages=messages,
        model=profile.model,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        response_mode=profile.response_mode,
        json_schema=json_schema if profile.response_mode == "json_schema" else None,
    )
