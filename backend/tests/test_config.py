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
