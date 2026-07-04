from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


def test_empty_optional_env_var_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Regression: a compose .env supplies DISCORD_GUILD_ID= (empty). Without
    # env_ignore_empty this crashes when pydantic parses "" as int | None.
    # chdir to a dir with no .env so only our env vars are in play.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_GUILD_ID", "")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "")

    settings = Settings()

    assert settings.discord_guild_id is None
    assert settings.trusted_proxy_ips == ""


def test_populated_optional_env_var_is_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_GUILD_ID", "123456789")

    settings = Settings()

    assert settings.discord_guild_id == 123456789
