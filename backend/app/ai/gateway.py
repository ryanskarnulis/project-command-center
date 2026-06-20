from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel

from app.ai.providers.base import BaseProvider, Message, ResponseMode
from app.ai.providers.ollama import OllamaProvider

logger = structlog.get_logger(__name__)

_PROFILES_PATH = Path(__file__).parent / "profiles.yaml"
# Runtime overrides written by the settings UI. Gitignored; deep-merged over the
# committed profiles.yaml (local wins per-field). The committed file is never edited.
_LOCAL_PROFILES_PATH = Path(__file__).parent / "profiles.local.yaml"
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


def _load_raw_merged() -> dict[str, dict[str, Any]]:
    """Committed profiles.yaml deep-merged with profiles.local.yaml (local wins).

    Profiles are flat field maps, so the merge is per-profile, per-field. The
    settings UI writes only to the local file; this is where its edits take effect.
    """
    base: dict[str, Any] = yaml.safe_load(_PROFILES_PATH.read_text()) or {}
    merged: dict[str, dict[str, Any]] = {name: dict(cfg) for name, cfg in base.items()}

    if _LOCAL_PROFILES_PATH.exists():
        local: dict[str, Any] = yaml.safe_load(_LOCAL_PROFILES_PATH.read_text()) or {}
        for name, cfg in local.items():
            merged.setdefault(name, {}).update(cfg)

    return merged


@lru_cache
def _load_profiles() -> dict[str, Profile]:
    return {name: Profile.model_validate(cfg) for name, cfg in _load_raw_merged().items()}


def reload_profiles() -> None:
    """Drop the cached profiles so the next read picks up local-override edits."""
    _load_profiles.cache_clear()


def get_profile(name: str) -> Profile:
    try:
        return _load_profiles()[name]
    except KeyError:
        logger.error("unknown_profile", profile=name)
        raise ValueError(f"unknown profile: {name!r}") from None


def _load_prompt(filename: str) -> str:
    # Read fresh on every call (not cached): the settings UI will edit prompt
    # files at runtime and those edits must take effect without a restart.
    return (_PROMPTS_DIR / filename).read_text()


def ollama_status() -> tuple[bool, str]:
    """(reachable, base_url) for the local Ollama runtime.

    Provider access stays inside the gateway per the constitution; the settings
    service calls this rather than importing the provider directly.
    """
    provider = OllamaProvider()
    return provider.is_reachable(), provider.base_url


def installed_models() -> list[str]:
    """Installed model names from Ollama (empty list when unreachable)."""
    return OllamaProvider().list_models()


def complete(
    *,
    profile_name: str,
    user_content: str,
    json_schema: dict[str, Any] | None = None,
    model_override: str | None = None,
) -> str:
    """Run a single model call through the named profile and return raw text.

    This is the only entry point workflows use to reach a model. The gateway
    stays generic — it does not know about any particular output schema. The
    caller passes ``json_schema`` (e.g. ``ExtractionOutput.model_json_schema()``)
    and the gateway forwards it to the provider's structured-output support.

    ``model_override`` is for benchmarking only (e.g. the eval harness comparing
    model sizes against one profile's prompt/temperature). Workflows never pass
    it — they resolve the model from the profile, keeping model names out of
    workflow code per the constitution.
    """
    profile = get_profile(profile_name)
    try:
        provider_cls = _PROVIDERS[profile.provider]
    except KeyError:
        logger.error("unknown_provider", provider=profile.provider, profile=profile_name)
        raise ValueError(f"unknown provider: {profile.provider!r}") from None

    messages: list[Message] = [
        {"role": "system", "content": _load_prompt(profile.system_prompt)},
        {"role": "user", "content": user_content},
    ]
    return provider_cls().complete(
        messages=messages,
        model=model_override or profile.model,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        response_mode=profile.response_mode,
        json_schema=json_schema if profile.response_mode == "json_schema" else None,
    )
