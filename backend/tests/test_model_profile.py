"""Per-model profiles (#402): loading, strictness, and what reaches the wire."""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import context_budget
from app.ai import loop as loop_module
from app.ai.loop import AgentLoop
from app.ai.model_profile import (
    DEFAULT_NAME,
    DEFAULT_PROFILE,
    PROFILES_DIR,
    ModelProfile,
    ProfileError,
    load_profile,
    parse_profile,
)
from app.ai.providers.llamacpp import LlamaCppProvider
from app.tools import runtime
from tests.scripted_provider import ScriptedProvider
from tests.scripted_provider import text_turn as _text
from tests.scripted_provider import tool_calls_turn as _calls


def _fleet_profile() -> Path | None:
    """``../agent-standard/model-profile.md`` beside the repo, if checked out.

    Found by walking up, so a git worktree under ``.claude/worktrees/`` finds
    it too. CI checks out this repo alone, so there the drift test skips (the
    sync-check job is what watches the fleet's files there).
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "agent-standard" / "model-profile.md"
        if candidate.is_file():
            return candidate
    return None


_FLEET_PROFILE = _fleet_profile()
# A row of the fleet profile's sampling table: | `top_k` | 64 (...) |
_FLEET_ROW = r"\|\s*`(temperature|top_p|top_k)`\s*\|\s*([\d.]+)"


def _sent(profile: ModelProfile, *, enable_thinking: bool = False) -> dict[str, Any]:
    bodies: list[dict[str, Any]] = []

    def server(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(server))
    provider = LlamaCppProvider(
        "http://llm.test/v1", profile.name, client=client, profile=profile
    )
    provider.chat([{"role": "user", "content": "hi"}], enable_thinking=enable_thinking)
    return bodies[0]


# --- the shipped profile -----------------------------------------------------


def test_gemma_profile_holds_the_shipped_numbers() -> None:
    gemma = load_profile("gemma-4-12b")
    assert gemma.name == "gemma-4-12b"
    assert gemma.source == "app/ai/profiles/gemma-4-12b.toml"
    assert gemma.sampling.wire() == {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
    assert gemma.thinking_kwarg == "enable_thinking"
    assert gemma.turn_max_tokens == loop_module.DEFAULT_MAX_TOKENS == 2048
    assert gemma.narrator_max_tokens == loop_module.NARRATOR_MAX_TOKENS == 2048


@pytest.mark.skipif(_FLEET_PROFILE is None, reason="needs the ../agent-standard sibling")
def test_gemma_matches_the_fleet_profile() -> None:
    # Copied, never edited here: drift is fixed by re-copying.
    assert _FLEET_PROFILE is not None
    text = _FLEET_PROFILE.read_text(encoding="utf-8")
    fleet = {key: float(value) for key, value in re.findall(_FLEET_ROW, text)}
    gemma = load_profile("gemma-4-12b")
    assert fleet == {key: float(value) for key, value in gemma.sampling.wire().items()}
    assert f'"{gemma.thinking_kwarg}"' in text


def test_every_profile_file_loads_and_is_named_for_its_model() -> None:
    files = sorted(PROFILES_DIR.glob("*.toml"))
    assert files, "no profiles shipped"
    for path in files:
        profile = load_profile(path.stem)
        assert profile.name == path.stem
        assert profile is not DEFAULT_PROFILE


def test_the_configured_default_model_has_a_profile() -> None:
    from app.config import Settings

    model = Settings.model_fields["llamacpp_model"].default
    assert (PROFILES_DIR / f"{model}.toml").is_file()


# --- strict loading ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "bogus = 1\n",  # unknown top-level key
        "[sampling]\ntemprature = 1.0\n",  # misspelt sampling key
        "[phases.plan]\nmax_tokens = 100\n",  # unknown phase
        "[phases.turn]\nmax_token = 100\n",  # misspelt phase key
        "[sampling]\ntop_k = 1.5\n",  # wrong type
        "[sampling]\ntop_p = 1.5\n",  # out of range
        'thinking_kwarg = true\n',  # wrong type
        "[phases.turn]\nmax_tokens = 0\n",
        # A cap above the completion reserve could overflow the window.
        f"[phases.turn]\nmax_tokens = {context_budget.COMPLETION_RESERVE_TOKENS + 1}\n",
        "[phases.narrator]\nmax_tokens = true\n",
        "sampling = 3\n",  # a table that is not one
        "[sampling\n",  # not TOML
    ],
)
def test_a_profile_that_says_anything_unknown_or_mistyped_fails(text: str) -> None:
    with pytest.raises(ProfileError):
        parse_profile("broken", text, "test")


def test_an_empty_profile_is_the_default_settings_under_its_own_name() -> None:
    profile = parse_profile("blank", "", "test")
    assert profile.name == "blank"
    assert profile.sampling.wire() == {}
    assert profile.thinking_kwarg is None
    assert profile.turn_max_tokens == DEFAULT_PROFILE.turn_max_tokens
    assert profile.narrator_max_tokens == DEFAULT_PROFILE.narrator_max_tokens


def test_a_model_with_no_profile_runs_on_the_default_and_says_so() -> None:
    load_profile.cache_clear()
    try:
        with structlog.testing.capture_logs() as logs:
            profile = load_profile("some-new-model")
    finally:
        load_profile.cache_clear()
    assert profile is DEFAULT_PROFILE
    assert profile.name == DEFAULT_NAME
    assert any(
        entry["event"] == "model_profile_missing"
        and entry["log_level"] == "warning"
        and entry["model"] == "some-new-model"
        for entry in logs
    )


# --- what reaches the wire ---------------------------------------------------


def test_the_default_profile_leaves_sampling_to_the_server() -> None:
    body = _sent(DEFAULT_PROFILE)
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_profile_sends_only_the_sampling_it_sets() -> None:
    profile = parse_profile("cool", "[sampling]\ntemperature = 0.3\n", "test")
    body = _sent(profile)
    assert body["temperature"] == 0.3
    assert "top_p" not in body and "top_k" not in body


def test_a_model_without_a_thinking_toggle_sends_no_template_kwargs() -> None:
    profile = parse_profile("plain", 'thinking_kwarg = ""\n', "test")
    assert "chat_template_kwargs" not in _sent(profile, enable_thinking=True)


def test_a_profile_names_its_own_thinking_key() -> None:
    profile = parse_profile("other", 'thinking_kwarg = "thinking"\n', "test")
    assert _sent(profile, enable_thinking=True)["chat_template_kwargs"] == {"thinking": True}


def test_the_provider_loads_its_model_s_profile_by_name() -> None:
    provider = LlamaCppProvider("http://llm.test/v1", "gemma-4-12b", client=httpx.Client())
    assert provider.profile is load_profile("gemma-4-12b")


# --- the loop runs at the profile's caps --------------------------------------


@pytest.fixture
def tool_db(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    factory: sessionmaker[Session] = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    monkeypatch.setattr(runtime, "session_factory", factory)
    yield factory


class _ProfiledProvider(ScriptedProvider):
    """A scripted provider that carries a model profile, as the real one does."""

    def __init__(self, turns: list[Any], profile: ModelProfile) -> None:
        super().__init__(turns)
        self.profile = profile
        self.model = profile.name
        self.thinking: list[bool] = []
        self.temperatures: list[float | None] = []

    def chat(self, messages: Any, *, enable_thinking: bool = False, **kwargs: Any) -> Any:
        self.thinking.append(enable_thinking)
        self.temperatures.append(kwargs.get("temperature"))
        return super().chat(messages, **kwargs)


class _Records:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


_TIGHT = parse_profile(
    "tight", "[phases.turn]\nmax_tokens = 300\n[phases.narrator]\nmax_tokens = 100\n", "test"
)


def test_the_loop_caps_turns_and_the_narrator_at_the_profile_s_values(
    tool_db: sessionmaker[Session],
) -> None:
    provider = _ProfiledProvider(
        [_calls(("list_projects", {})), _text("I looked; nothing there.")], _TIGHT
    )
    tracer = _Records()
    result = AgentLoop(provider, max_iterations=1, tracer=tracer).run("What projects?")
    assert result.stop_reason == "max_iterations"
    assert [request["max_tokens"] for request in provider.requests] == [300, 100]
    (record,) = tracer.records
    assert record["serving"]["profile"] == "tight"
    assert record["limits"]["max_tokens"] == 300
    assert record["limits"]["narrator_max_tokens"] == 100


def test_an_explicit_cap_overrides_the_profile_and_bounds_the_narrator(
    tool_db: sessionmaker[Session],
) -> None:
    provider = _ProfiledProvider(
        [_calls(("list_projects", {})), _text("I looked; nothing there.")], _TIGHT
    )
    AgentLoop(provider, max_iterations=1, max_tokens=64, tracer=_Records()).run("What projects?")
    assert [request["max_tokens"] for request in provider.requests] == [64, 64]


def test_a_thinking_profile_thinks_on_turns_and_never_on_the_narrator(
    tool_db: sessionmaker[Session],
) -> None:
    # #417: `[phases.turn] thinking = true` opens the turns' thinking channel;
    # the narrator stays a plain call.
    thinking = parse_profile("thinker", "[phases.turn]\nthinking = true\n", "test")
    assert thinking.turn_thinking and thinking.describe()["turn_thinking"] is True
    provider = _ProfiledProvider(
        [_calls(("list_projects", {})), _text("I looked; nothing there.")], thinking
    )
    AgentLoop(provider, max_iterations=1, tracer=_Records()).run("What projects?")
    assert provider.thinking == [True, False]


def test_the_default_and_gemma_profiles_never_think(tool_db: sessionmaker[Session]) -> None:
    assert not DEFAULT_PROFILE.turn_thinking
    provider = _ProfiledProvider([_text("Hi."), _text("Hey.")], load_profile("gemma-4-12b"))
    AgentLoop(provider, tracer=_Records()).run("hi")
    assert provider.thinking == [False, False]


def test_a_planner_temperature_is_sent_on_planner_calls_only(
    tool_db: sessionmaker[Session],
) -> None:
    # #418: `[phases.turn] temperature` cools the planner; the narrator keeps
    # `[sampling]`'s, which the provider sends when no override is given.
    cool = parse_profile("cool", "[phases.turn]\ntemperature = 0.3\n", "test")
    assert cool.describe()["turn_temperature"] == 0.3
    provider = _ProfiledProvider([_calls(("list_projects", {})), _text("None.")], cool)
    AgentLoop(provider, tracer=_Records()).run("What projects?")
    assert provider.temperatures == [0.3, 0.3, None]
    assert load_profile("gemma-4-12b").turn_temperature is None
