"""How much prior conversation may reach the model, and which turns survive.

Pure policy: no DB, no provider, no imports from the loop — so both the
service layer (which decides what to *read* out of a conversation) and the
loop (the last gate before a provider request) can apply the identical rule.

The problem this bounds (#244): a per-turn cap alone doesn't bound a
conversation. 200 individually valid 8,000-character turns is 1.6M characters
in one request. The runtime is configured ``-c 131072`` (docs/agent-design.md
"Runtime"), so a long enough thread either overflows the window outright or
spends the entire run budget prefilling it — and every retry resends the same
oversized prompt, leaving the user no in-thread recovery.

The strategy is a **deterministic recent window**: keep the newest turns that
fit, drop the older ones, and never touch what is persisted. The full
transcript stays in ``conversation_messages`` for display and audit; this only
governs what is sent out. No summarizer — that would mean a second model call
per turn on the same local GPU, and CLAUDE.md rule 3 rules out a cloud one.

Token counting is a character heuristic, on purpose. A real tokenizer means a
new dependency (CLAUDE.md: ask first) plus keeping it in sync with whatever
GGUF llama-swap has loaded. :data:`CHARS_PER_TOKEN` is set *below* the true
ratio so the estimate over-counts tokens and the budget errs small; being
wrong in that direction costs a little context, while erring large costs a
failed run.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog

from app.schemas.conversations import MAX_AGENT_MESSAGE_LENGTH

logger = structlog.get_logger(__name__)

# The runtime's configured context window: llama-swap serves gemma-4-12b with
# `-c 131072` (../llama-swap/config.yaml; docs/agent-design.md "Runtime").
MODEL_CONTEXT_TOKENS = 131_072

# Characters per estimated token. English prose runs ~4 chars/token on
# SentencePiece-family tokenizers and JSON/ids run worse; 3 deliberately
# under-states the ratio so `estimate_tokens` over-states the count. Every
# budget below is therefore a *floor* on what actually fits.
CHARS_PER_TOKEN = 3

# Per-message framing (role tags, delimiters) the chat template adds around
# each turn's content. Small, but it is what makes a window of thousands of
# one-word turns cost something.
MESSAGE_FRAMING_TOKENS = 4

# --- Reserves: window space history may not consume -------------------------

# System prompt + the tool JSON Schemas sent on every request. Measured at
# 2,426 + 14,269 chars (~5.6k estimated tokens) for 25 tools; the ceiling
# leaves room for the registry to grow. `test_context_budget.py` asserts the
# real overhead still fits, so this can't silently go stale.
STATIC_PROMPT_RESERVE_TOKENS = 8_192

# The turn being sent right now, reserved at its schema maximum
# (MAX_AGENT_MESSAGE_LENGTH) rather than its actual size, so the current user
# message fits by construction whatever the caller sends.
CURRENT_TURN_RESERVE_TOKENS = -(-MAX_AGENT_MESSAGE_LENGTH // CHARS_PER_TOKEN)

# The run's *own* tool traffic: up to `max_iterations` assistant turns with
# tool calls plus their results, all appended to the same request as the loop
# proceeds. History has to leave room for it or the loop dies mid-run.
TOOL_TRANSCRIPT_RESERVE_TOKENS = 16_384

# Room for the final reply.
COMPLETION_RESERVE_TOKENS = 2_048

# --- Prefill cap: the binding constraint in practice ------------------------

# Window space is not the tightest bound — time is. `agent_run_budget_seconds`
# defaults to 240 s and prefill measures 446–680 tok/s at depth
# (docs/agent-design.md "Runtime"), so a full-window prompt is ~5 minutes of
# prefill and blows the deadline before the first token. 24,576 tokens is
# ~55 s at the pessimistic 446 tok/s — under a quarter of the run budget,
# leaving the rest for tool iterations and generation.
PREFILL_BUDGET_TOKENS = 24_576

# Ceiling on rows read per history build, so an enormous thread never lands in
# memory whole. Far more messages than the token budget can hold unless every
# turn is a couple of words, and those oldest rows would be dropped anyway.
HISTORY_SCAN_LIMIT = 500

DEFAULT_PROMPT_OVERHEAD_TOKENS = (
    STATIC_PROMPT_RESERVE_TOKENS + CURRENT_TURN_RESERVE_TOKENS
)


def estimate_tokens(text: str) -> int:
    """Conservative token count for ``text`` — see :data:`CHARS_PER_TOKEN`."""
    return -(-len(text) // CHARS_PER_TOKEN)


def history_token_budget(
    *, prompt_overhead_tokens: int = DEFAULT_PROMPT_OVERHEAD_TOKENS
) -> int:
    """Tokens of prior conversation one request may carry.

    ``prompt_overhead_tokens`` is everything in the request that is *not*
    history: system prompt, tool schemas, and the current user turn. Callers
    that can measure it (the loop) pass the real figure; callers that can't
    (the service layer, which has no provider in hand) take the reserved
    default, which is sized above the measured overhead.

    Never negative: a pathological overhead yields a zero budget — no history
    — rather than an impossible request.
    """
    window_headroom = (
        MODEL_CONTEXT_TOKENS
        - prompt_overhead_tokens
        - TOOL_TRANSCRIPT_RESERVE_TOKENS
        - COMPLETION_RESERVE_TOKENS
    )
    return max(0, min(window_headroom, PREFILL_BUDGET_TOKENS))


def fit_history(
    history: Sequence[dict[str, Any]], *, budget_tokens: int
) -> list[dict[str, Any]]:
    """The newest coherent suffix of ``history`` that fits ``budget_tokens``.

    Deterministic and idempotent: walk from the newest turn backwards, keep
    each turn whose estimated cost still fits, and stop at the first that does
    not — never skip a turn to squeeze a smaller older one in, which would
    interleave answers with unrelated questions.

    Coherence: the kept window is trimmed to start on a ``user`` turn, so an
    assistant reply is never stranded without the question it answered. A
    window whose newest turn alone exceeds the budget comes back empty; that
    is the deterministic floor, and the current turn and system prompt still
    fit because they were reserved out of the budget before this was called.
    """
    kept: list[dict[str, Any]] = []
    spent = 0
    for message in reversed(history):
        content = message.get("content") or ""
        cost = estimate_tokens(str(content)) + MESSAGE_FRAMING_TOKENS
        if spent + cost > budget_tokens:
            break
        spent += cost
        kept.append(message)
    kept.reverse()
    while kept and kept[0].get("role") != "user":
        kept.pop(0)
    if len(kept) < len(history):
        logger.info(
            "agent_history_windowed",
            kept_messages=len(kept),
            dropped_messages=len(history) - len(kept),
            budget_tokens=budget_tokens,
            estimated_tokens=spent,
        )
    return kept
