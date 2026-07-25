from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


def test_empty_optional_env_var_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A compose .env may supply a var with an empty value (e.g. APP_ENV=).
    # Without env_ignore_empty this would override the typed default with "".
    # chdir to a dir with no .env so only our env vars are in play.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "")

    settings = Settings()

    assert settings.app_env == "development"  # empty env ignored → default wins


def test_populated_optional_env_var_is_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")

    settings = Settings()

    assert settings.app_env == "production"


def test_empty_speech_base_url_reads_as_voiceless(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A compose .env may carry `SPEECH_BASE_URL=` to opt out of voice; that
    # must reach Settings as "no configuration", not as a bogus empty URL.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPEECH_BASE_URL", "")
    monkeypatch.setenv("TTS_BASE_URL", "")

    settings = Settings()

    assert settings.speech_base_url is None
    assert settings.tts_base_url is None


def test_compose_leaves_speech_urls_unset_by_default() -> None:
    """Voiceless must be reachable through docker compose (issue #101).

    A ``${SPEECH_BASE_URL:-http://...}`` style default makes both unset and
    empty expand to a configured URL, so the documented voiceless mode was
    impossible to reach. Assert compose substitutes no URL of its own.
    """
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()

    for var in ("SPEECH_BASE_URL", "TTS_BASE_URL"):
        line = next(
            stripped
            for raw in compose.splitlines()
            if (stripped := raw.strip()).startswith(f"{var}:")
        )
        _, _, value = line.partition(":")
        assert value.strip() in {
            f"${{{var}:-}}",
            f"${{{var}-}}",
            f"${{{var}}}",
        }, f"{var} must expand to empty when unset, got {value.strip()!r}"
