"""Model profiles: what the agent knows about one model, kept out of the code (#402).

A profile is one TOML file in ``app/ai/profiles/``, named for the llama-swap
model id it describes, and picked by ``LLAMACPP_MODEL``. It holds what changes
with the model and nothing else:

- ``thinking_kwarg`` — the ``chat_template_kwargs`` key that turns the model's
  thinking channel on and off. Absent (or ``""``) for a model with no toggle:
  the request then carries no ``chat_template_kwargs`` at all.
- ``[sampling]`` — the request's ``temperature``, ``top_p`` and ``top_k``. A
  key left out is a field the request leaves out, so the server's own default
  applies.
- ``[phases.turn]`` / ``[phases.narrator]`` — the per-call ``max_tokens`` cap
  (#397) for the planner's tool-calling turns and for the narrator's one
  tool-free call (#418).
  Each cap must fit the completion reserve the context budget keeps free
  (``context_budget.COMPLETION_RESERVE_TOKENS``), or a capped call could
  overflow the window. ``[phases.turn]`` may also set ``thinking = true``: the
  loop's turns then open the thinking channel (#417). Its tokens count against
  the same cap. The narrator never thinks. ``[phases.turn] temperature``
  (#418) sets the planner's calls apart from ``[sampling]``'s, which the
  narrator keeps.

Loading is strict: an unknown key, a misspelt phase or a value of the wrong
type is a :class:`ProfileError`, because a typo in a profile is a model quietly
run on settings nobody chose. A model with no file runs on
:data:`DEFAULT_PROFILE` — no sampling opinion, today's caps, the common
thinking toggle — and a warning, because nobody has measured it here yet.

The gemma-4-12b file is the shipped behavior: its sampling is the fleet's
(``../agent-standard/model-profile.md``, copied, never edited here), and
``tests/test_request_bytes.py`` pins that it sends the bytes it sent before
profiles existed.
"""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai import context_budget

logger = structlog.get_logger(__name__)

PROFILES_DIR = Path(__file__).with_name("profiles")

# What a profile is named when no file described the model.
DEFAULT_NAME = "default"

# The per-call caps a model with no profile runs at — the values the loop
# shipped with (#397). A turn's cap is the completion reserve itself, and so
# is the narrator's (#418): it writes the reply the turn used to.
DEFAULT_TURN_MAX_TOKENS = context_budget.COMPLETION_RESERVE_TOKENS
DEFAULT_NARRATOR_MAX_TOKENS = context_budget.COMPLETION_RESERVE_TOKENS

# Sampling keys in the order the request carries them.
SAMPLING_KEYS = ("temperature", "top_p", "top_k")


class ProfileError(ValueError):
    """A profile file that does not say what a profile must."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Sampling(_Strict):
    """Only the keys a profile sets go on the wire; ``None`` is the server's."""

    temperature: float | None = Field(default=None, ge=0)
    top_p: float | None = Field(default=None, gt=0, le=1)
    # Not an OpenAI field: llama-server reads it as a plain body field.
    top_k: int | None = Field(default=None, ge=0)

    def wire(self) -> dict[str, float | int]:
        """The sampling fields the request sends, in :data:`SAMPLING_KEYS` order."""
        values = self.model_dump()
        return {key: values[key] for key in SAMPLING_KEYS if values[key] is not None}


class TurnPhase(_Strict):
    """The planner's tool-calling turns."""

    max_tokens: int = Field(
        default=DEFAULT_TURN_MAX_TOKENS, ge=1, le=context_budget.COMPLETION_RESERVE_TOKENS
    )
    # The turn reasons before it answers (#417). Off unless a profile says so.
    thinking: bool = False
    # The planner's own temperature (#418), over ``[sampling]``'s for its
    # calls only; the narrator keeps ``[sampling]``'s. Unset: the same.
    temperature: float | None = Field(default=None, ge=0)


class NarratorPhase(_Strict):
    """The narrator's one tool-free call (#418)."""

    max_tokens: int = Field(
        default=DEFAULT_NARRATOR_MAX_TOKENS, ge=1, le=context_budget.COMPLETION_RESERVE_TOKENS
    )


class Phases(_Strict):
    turn: TurnPhase = TurnPhase()
    narrator: NarratorPhase = NarratorPhase()


class _ProfileFile(_Strict):
    """The TOML file's shape, exactly — nothing else is accepted."""

    thinking_kwarg: str = ""
    sampling: Sampling = Sampling()
    phases: Phases = Phases()


class ModelProfile(BaseModel):
    """One model's settings, resolved. ``source`` says where they came from."""

    model_config = ConfigDict(frozen=True)

    name: str
    source: str
    thinking_kwarg: str | None = "enable_thinking"
    sampling: Sampling = Sampling()
    turn_max_tokens: int = DEFAULT_TURN_MAX_TOKENS
    turn_thinking: bool = False
    turn_temperature: float | None = None
    narrator_max_tokens: int = DEFAULT_NARRATOR_MAX_TOKENS

    def describe(self) -> dict[str, Any]:
        """The profile as the eval harness prints it."""
        return {
            "name": self.name,
            "source": self.source,
            "thinking_kwarg": self.thinking_kwarg,
            "sampling": self.sampling.wire(),
            "turn_max_tokens": self.turn_max_tokens,
            "turn_thinking": self.turn_thinking,
            "turn_temperature": self.turn_temperature,
            "narrator_max_tokens": self.narrator_max_tokens,
        }


DEFAULT_PROFILE = ModelProfile(name=DEFAULT_NAME, source="built in")


def parse_profile(name: str, text: str, source: str) -> ModelProfile:
    """A profile from its TOML text. Strict: anything unknown or mistyped fails."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"profile {name!r}: {exc}") from exc
    try:
        parsed = _ProfileFile.model_validate(data)
    except ValidationError as exc:
        raise ProfileError(f"profile {name!r} ({source}): {exc}") from exc
    return ModelProfile(
        name=name,
        source=source,
        thinking_kwarg=parsed.thinking_kwarg or None,
        sampling=parsed.sampling,
        turn_max_tokens=parsed.phases.turn.max_tokens,
        turn_thinking=parsed.phases.turn.thinking,
        turn_temperature=parsed.phases.turn.temperature,
        narrator_max_tokens=parsed.phases.narrator.max_tokens,
    )


@cache
def load_profile(model: str) -> ModelProfile:
    """The profile for ``model``, or :data:`DEFAULT_PROFILE` with a warning when
    no file describes it. Cached: a profile is read once per process."""
    path = PROFILES_DIR / f"{model}.toml"
    if not path.is_file():
        logger.warning("model_profile_missing", model=model, using=DEFAULT_NAME)
        return DEFAULT_PROFILE
    return parse_profile(model, path.read_text(encoding="utf-8"), f"app/ai/profiles/{path.name}")
