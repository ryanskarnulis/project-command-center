"""What came before this run, as data for a model phase (#418).

Neither phase is handed the conversation as chat turns. Each gets one
:class:`Recall`, rendered as data inside its own prompt under "Before this
run:" — at the head of the planner's opening user message and of the
narrator's brief. Ported from chess's ``conversation.Recall`` (chess#372,
``docs/turn-memory.md``), in the same three parts:

1. **The app's record of earlier runs** — every write an earlier run made
   (done) or had refused, from the tool calls stored with each assistant turn
   (:mod:`app.ai.run_record` sorts them). Reads are left out, found or not:
   what they answered is stale by now, and the tools re-read live state.
2. **What the user asked earlier, in their own words** — every user turn but
   the last exchange's, newest kept under a size cap with a count of what went.
3. **The last exchange, whole** — what "it", "the first one" and "the second
   one" point at. The reply is labelled as words, not a record.

**Why not chat turns.** A model reading Glitch's earlier replies as its own
past turns takes a false one as fact: the frontier's ``poisoned_*`` scenarios
(``answered_from_memory``) are a last reply that falsely said a task was done,
or does not exist, and the model answering from it without a tool. Here the
record says what the tools did, and the reply sits beside it as words.

No model writes any of it: the record is code's, the requests and the last
exchange are copied words. Pure: no session, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.ai import run_record

# The user's earlier requests: each cut to this many characters, and the
# newest kept until this many in all (chess's ``REQUEST_CHARS`` /
# ``REQUESTS_MAX_CHARS``).
REQUEST_CHARS = 160
REQUESTS_MAX_CHARS = 1500

# The record of earlier runs: the newest lines kept until this many characters.
RECORD_MAX_CHARS = 2000

# The last exchange is shown whole in practice, cut only when a wall of text
# would crowd out the prompt. Twice chess's 600: a PCC reply that lists tasks
# is what "the second one" counts through.
LAST_EXCHANGE_CHARS = 1200

# How each phase's prompt names the last reply: words, not a record — what
# the tools did is, and a line said last turn is never a fact to repeat. The
# narrator is Glitch, so to it the reply is what *it* said.
NARRATOR_REPLY_LABEL = "What you said then (your words, not a record)"
PLANNER_REPLY_LABEL = "The assistant said then (its words, not a record)"


@dataclass(frozen=True)
class Recall:
    """What came before this run, in the three parts code keeps. Each phase
    renders it for itself (:meth:`render`), because the last reply is the
    narrator's own words and only words to the planner."""

    record: tuple[str, ...] = ()
    requests: tuple[str, ...] = ()
    last: tuple[str, str | None] | None = None

    @classmethod
    def of(cls, history: Sequence[dict[str, Any]]) -> Recall:
        """Built from stored turns: ``{"role", "content"}`` plus, on an
        assistant turn, the run's ``tool_calls`` as stored
        (``ToolCallRecord.model_dump()``). ``content`` may be ``None`` — a run
        that ended without a reply still did what its record shows."""
        last = _last_exchange(history)
        return cls(
            record=tuple(_earlier_record(history)),
            requests=tuple(_requests(history, skip_last=last is not None)),
            last=last,
        )

    def render(self, reply_label: str) -> str:
        """The parts under their labels, empty ones left out; "" when there is
        nothing before this run."""
        parts = []
        if self.record:
            parts.append(
                "The app's record of earlier runs (what the tools did, oldest first):\n"
                + "\n".join(f"- {line}" for line in self.record)
            )
        if self.requests:
            parts.append(
                "What the user asked earlier, in their own words, oldest first:\n"
                + "\n".join(
                    f"- {r}" if r.startswith("(") else f'- "{r}"' for r in self.requests
                )
            )
        if self.last is not None:
            said, reply = self.last
            said = _truncate(_flat(said), LAST_EXCHANGE_CHARS)
            line = (
                f'{reply_label}: "{_truncate(_flat(reply), LAST_EXCHANGE_CHARS)}"'
                if reply
                else f"{reply_label}: nothing."
            )
            parts.append(f'The last exchange:\nThe user said: "{said}"\n{line}')
        return "\n\n".join(parts)


def section(recall: Recall, reply_label: str) -> str | None:
    """The block a phase's prompt opens with, or ``None`` before any history."""
    rendered = recall.render(reply_label)
    return f"Before this run:\n\n{rendered}" if rendered else None


def _flat(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    """Cut on a word boundary, marked: half a title is worse than a shorter line."""
    if len(text) <= limit:
        return text
    head = text[: limit + 1]
    cut = head.rfind(" ")
    return f"{text[:cut].rstrip() if cut > 0 else text[:limit]}…"


def _last_exchange(history: Sequence[dict[str, Any]]) -> tuple[str, str | None] | None:
    """The newest (user words, reply) pair; ``None`` before there is one."""
    for index in range(len(history) - 1, 0, -1):
        if history[index]["role"] == "assistant" and history[index - 1]["role"] == "user":
            return str(history[index - 1]["content"] or ""), history[index].get("content")
    return None


def _requests(history: Sequence[dict[str, Any]], *, skip_last: bool) -> list[str]:
    """The user's words from every turn but the last exchange's, oldest first.
    Past the cap the oldest go, and the first line says how many, because a
    memory that quietly forgets reads like one that never heard."""
    users = [
        index
        for index, message in enumerate(history)
        if message["role"] == "user" and message.get("content")
    ]
    if skip_last:
        last_reply = max(i for i, m in enumerate(history) if m["role"] == "assistant")
        users = [index for index in users if index != last_reply - 1]
    requests = [_truncate(_flat(str(history[index]["content"])), REQUEST_CHARS) for index in users]
    return _newest_within(requests, REQUESTS_MAX_CHARS, "earlier requests")


def _earlier_record(history: Sequence[dict[str, Any]]) -> list[str]:
    """One line per write an earlier run made or had refused, oldest first."""
    from app.ai.loop import ToolCallRecord

    lines: list[str] = []
    asked = ""
    for message in history:
        if message["role"] == "user":
            asked = str(message.get("content") or "")
            continue
        for stored in message.get("tool_calls") or []:
            record = ToolCallRecord.model_validate(stored)
            outcome = run_record.classify(record)
            # Writes only: a read's answer (or its "not found") is stale now.
            if outcome not in ("done", "refused") or record.tool in run_record.READ_TOOLS:
                continue
            call = f"{record.tool} {run_record.clip(_compact(record.arguments))}"
            if outcome == "done":
                line = f"{call} -> {run_record.answer_brief(record.result or '')}"
            else:
                line = f"refused: {call} -> {run_record.clip(record.error or '')}"
            lines.append(f'{line} (asked: "{_truncate(_flat(asked), 80)}")')
    return _newest_within(lines, RECORD_MAX_CHARS, "earlier lines")


def _newest_within(items: list[str], max_chars: int, what: str) -> list[str]:
    kept: list[str] = []
    total = 0
    for item in reversed(items):
        total += len(item)
        if total > max_chars and kept:
            break
        kept.append(item)
    kept.reverse()
    dropped = len(items) - len(kept)
    return ([f"({dropped} {what} not listed)"] if dropped else []) + kept


def _compact(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, separators=(",", ":"), sort_keys=True, default=str)
