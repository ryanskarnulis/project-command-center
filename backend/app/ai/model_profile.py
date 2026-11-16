"""PCC's model profiles: what the agent knows about one model, kept out of the code (#402).

The profile format and its strict loader are the fleet's, vendored verbatim in
:mod:`app.ai.agentkit.model_profile` (canonical in
``../agent-standard/agentkit/``). This module is what is PCC's own: its phases
(:data:`SPEC`), its profile files, the cache, and the warning for a model
nobody has measured here.

A profile is one TOML file in ``app/ai/profiles/``, named for the llama-swap
model id it describes, and picked by ``LLAMACPP_MODEL``. PCC has two phases:

- ``[phases.turn]`` — the planner's tool-calling turns: the per-call
  ``max_tokens`` cap (#397), ``thinking = true`` to open the thinking channel
  on them (#417; its tokens count against the same cap), and ``temperature``
  (#418) to set the planner's calls apart from ``[sampling]``'s.
- ``[phases.narrator]`` — the narrator's one tool-free call (#418): its
  ``max_tokens`` cap, and nothing else. The narrator never thinks and keeps
  ``[sampling]``'s temperature, so either key there is an error.

Each cap must fit the completion reserve the context budget keeps free
(``context_budget.COMPLETION_RESERVE_TOKENS``), or a capped call could overflow
the window.

A model with no file runs on :data:`DEFAULT_PROFILE` — no sampling opinion,
today's caps, the common thinking toggle — and a warning.

The gemma-4-12b file is the shipped behavior: its sampling is the fleet's
(``../agent-standard/model-profile.md``, copied, never edited here), and
``tests/test_request_bytes.py`` pins that it sends the bytes it sent before
profiles existed.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import structlog

from app.ai import context_budget
from app.ai.agentkit.model_profile import (
    DEFAULT_NAME,
    ModelProfile,
    PhaseSettings,
    ProfileError,
    ProfileSpec,
)

__all__ = [
    "DEFAULT_NAME",
    "DEFAULT_NARRATOR_MAX_TOKENS",
    "DEFAULT_PROFILE",
    "DEFAULT_TURN_MAX_TOKENS",
    "NARRATOR",
    "PROFILES_DIR",
    "SPEC",
    "TURN",
    "ModelProfile",
    "PhaseSettings",
    "ProfileError",
    "load_profile",
    "parse_profile",
]

logger = structlog.get_logger(__name__)

PROFILES_DIR = Path(__file__).with_name("profiles")

# PCC's phases: the planner's tool-calling turns, and the narrator's one call.
TURN = "turn"
NARRATOR = "narrator"

# The per-call caps a model with no profile runs at — the values the loop
# shipped with (#397). A turn's cap is the completion reserve itself, and so
# is the narrator's (#418): it writes the reply the turn used to.
DEFAULT_TURN_MAX_TOKENS = context_budget.COMPLETION_RESERVE_TOKENS
DEFAULT_NARRATOR_MAX_TOKENS = context_budget.COMPLETION_RESERVE_TOKENS

SPEC = ProfileSpec(
    phases={
        TURN: PhaseSettings(max_tokens=DEFAULT_TURN_MAX_TOKENS),
        NARRATOR: PhaseSettings(max_tokens=DEFAULT_NARRATOR_MAX_TOKENS),
    },
    phase_keys={TURN: ("temperature", "thinking")},
    max_tokens_limit=context_budget.COMPLETION_RESERVE_TOKENS,
)

DEFAULT_PROFILE = SPEC.default_profile()


def parse_profile(name: str, text: str, source: str) -> ModelProfile:
    """A profile from its TOML text. Strict: anything unknown or mistyped fails."""
    return SPEC.parse(name, text, source)


@cache
def load_profile(model: str) -> ModelProfile:
    """The profile for ``model``, or :data:`DEFAULT_PROFILE` with a warning when
    no file describes it. Cached: a profile is read once per process."""
    profile = SPEC.load(PROFILES_DIR, model, "app/ai/profiles")
    if profile is None:
        logger.warning("model_profile_missing", model=model, using=DEFAULT_NAME)
        return DEFAULT_PROFILE
    return profile
