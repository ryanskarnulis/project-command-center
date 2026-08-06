from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize("env_var", ["AGENT_MESSAGES_PER_MIN", "VOICE_REQUESTS_PER_MIN"])
@pytest.mark.parametrize("value", ["0", "-1", "-30"])
def test_non_positive_rate_limits_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env_var: str, value: str
) -> None:
    """Issue #165: a non-positive cap made the limiter index an empty deque.

    Non-positive is invalid configuration (zero does NOT disable the limiter),
    so it must fail loudly at the settings boundary, not per request.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(env_var, value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("env_var", ["AGENT_MESSAGES_PER_MIN", "VOICE_REQUESTS_PER_MIN"])
def test_positive_rate_limits_are_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env_var: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(env_var, "1")

    settings = Settings()

    assert getattr(settings, env_var.lower()) == 1


@pytest.mark.parametrize("value", ["0", "-1", "-2", "-240.5", "inf", "-inf", "nan"])
def test_invalid_agent_run_budget_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    """Issue #172: the budget is a lock timeout and a wall-clock deadline.

    0 times out instantly, -1 waits forever (defeating the ceiling), < -1 raises
    ValueError at request time, and inf/nan disable the deadline. All must fail
    at the settings boundary instead.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_RUN_BUDGET_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["0.05", "240", "600.5"])
def test_positive_agent_run_budget_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_RUN_BUDGET_SECONDS", value)

    assert Settings().agent_run_budget_seconds == float(value)


@pytest.mark.parametrize("value", ["0", "-1", "-300", "-0.5", "inf", "-inf", "nan"])
def test_invalid_llamacpp_timeout_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    """Issue #262: the timeout is handed straight to httpx.Timeout.

    httpx accepts 0, negatives, inf and nan without complaint, so a typo turns
    into either an instant timeout on every provider call or an unbounded wait
    on a stuck llama-server. Reject it at the settings boundary, exactly as
    AGENT_RUN_BUDGET_SECONDS is (#172).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLAMACPP_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["0.05", "300", "600.5"])
def test_positive_llamacpp_timeout_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLAMACPP_TIMEOUT_SECONDS", value)

    assert Settings().llamacpp_timeout_seconds == float(value)


@pytest.mark.parametrize("value", ["0", "-1", "65536", "99999"])
def test_out_of_range_api_port_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    """Issue #262: port 0 silently binds an ephemeral port.

    The API then listens somewhere nobody is looking, and out-of-range values
    only fail later at socket bind. Both are startup-time config errors.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("API_PORT", value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["1", "8101", "65535"])
def test_in_range_api_port_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("API_PORT", value)

    assert Settings().api_port == int(value)
