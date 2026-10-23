"""The open question: which task the user meant, asked and not yet answered (#420).

When the user's words fit several tasks, the planner used to have only prose
to ask with, and the next turn had to recover what was on offer from the
narrator's wording: "the second one" counted through a reply that might name
the tasks in another order, or not by title at all. The frontier's
``ambiguous_then_pick`` scored 0/20 on that.

Now the planner calls ``ask_user`` with the candidates' ids. The registry
checks they exist and the run ends on the question (``AgentLoop``), which the
narrator asks, naming the candidates in the order given. Ported from chess's
``clarification.py`` (chess#319) and ``ask_player`` (chess#289).

**Code owns whether the question is still open; the model owns what the user
meant.** Nothing here reads the user's words, and nothing here ever acts on a
candidate. The answer is the planner's ordinary ``complete_task`` or
``update_task``, checked by the service layer like any other.

The record is the landed ``ask_user`` call itself, stored with its run's
assistant turn like every tool call, so it needs no table of its own. The
next run's state block carries it (:func:`render`) while it is **open**:

- no later run in the conversation landed a write. A run that wrote either
  answered it or moved on, so the question is closed. Reads and chat
  ("which one is due sooner?") leave it standing;
- every candidate is still there and unchanged since the ask
  (:func:`staleness`): one trashed, completed or edited from anywhere else
  means the choice offered is no longer the one on screen. Checked against
  live rows at the start of each run, the way chess checks its board version.

Pure: no session, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# The tool's registered name (``app/tools/registry.py``).
ASK_TOOL = "ask_user"

# Why a question is no longer open, as the trace names it.
ANSWERED_OR_MOVED_ON = "later_write"
CANDIDATE_GONE = "candidate_gone"
CANDIDATE_CHANGED = "candidate_changed"

# The longest title the state block shows for a candidate (the block's own cap
# for project names, ``state_block.MAX_NAME_CHARS``).
_MAX_TITLE_CHARS = 60


@dataclass(frozen=True)
class Candidate:
    """One task the user was offered, as it was when asked."""

    id: int
    title: str
    updated_at: str


@dataclass(frozen=True)
class OpenQuestion:
    """The newest landed ask in a conversation and what prompted it.

    ``candidates`` keep the order the planner gave them, which is the order
    the narrator named them in, so "the second one" is ``candidates[1]``.
    """

    request: str
    candidates: tuple[Candidate, ...]

    def trace(self) -> dict[str, Any]:
        return {"request": self.request, "candidates": [c.id for c in self.candidates]}


def candidates_of(result: str) -> tuple[Candidate, ...]:
    """The candidates a landed ``ask_user`` answered with (its stored result,
    a list of task read models), in order. Empty for an answer it can't read."""
    try:
        rows = json.loads(result)
    except ValueError:
        return ()
    if not isinstance(rows, list):
        return ()
    return tuple(
        Candidate(id=row["id"], title=str(row.get("title") or ""), updated_at=str(row.get("updated_at") or ""))
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), int)
    )


def from_history(history: Sequence[Mapping[str, Any]]) -> tuple[OpenQuestion | None, str]:
    """The question left open by earlier runs, before checking live rows:
    the newest landed ask, unless a write landed after it — then ``None`` and
    :data:`ANSWERED_OR_MOVED_ON`. ``(None, "")`` when nothing was asked.

    ``history`` is the stored turns (``conversations.history_for_loop``): an
    assistant turn carries its run's ``tool_calls`` as
    ``ToolCallRecord.model_dump()``.
    """
    from app.ai import run_record
    from app.ai.loop import ToolCallRecord

    asked: OpenQuestion | None = None
    closed = ""
    request = ""
    for message in history:
        if message.get("role") == "user":
            request = str(message.get("content") or "")
            continue
        for stored in message.get("tool_calls") or []:
            record = ToolCallRecord.model_validate(stored)
            if record.tool == ASK_TOOL and record.result is not None:
                candidates = candidates_of(record.result)
                asked = OpenQuestion(request, candidates) if candidates else None
                closed = ""
            elif asked is not None and run_record.classify(record) == "done":
                asked, closed = None, ANSWERED_OR_MOVED_ON
    return asked, closed


def staleness(question: OpenQuestion, live: Mapping[int, str]) -> str:
    """Why ``question`` no longer stands, or "" when it does. ``live`` maps
    each candidate still active to its ``updated_at`` now, as the read model
    serializes it; a candidate missing from it was trashed."""
    for candidate in question.candidates:
        if candidate.id not in live:
            return CANDIDATE_GONE
        if live[candidate.id] != candidate.updated_at:
            return CANDIDATE_CHANGED
    return ""


def render(question: OpenQuestion) -> list[str]:
    """The state block's lines for an open question."""
    lines = [
        "Open question: the assistant asked the user which task they meant and has not had an "
        f'answer. They had said: "{_clip(question.request, 160)}". The tasks, in the order they '
        "were named:"
    ]
    lines += [
        f"{number}. id {candidate.id} · {_clip(candidate.title, _MAX_TITLE_CHARS)}"
        for number, candidate in enumerate(question.candidates, start=1)
    ]
    return lines


def ask_text(candidates: Sequence[Candidate]) -> str:
    """The question in code's own words, for when the narrator cannot speak."""
    named = "\n".join(
        f"{number}. {candidate.title}" for number, candidate in enumerate(candidates, start=1)
    )
    return f"Which task did you mean?\n{named}"


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
