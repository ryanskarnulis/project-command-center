"""The run record: what a run did, built by code from its tool calls.

The narrator (#418, :mod:`app.ai.narration`) speaks from this record, not from
the planner's words, and so does a limit stop (#399): out of turns, out of
corrections, a cut-off reply, a stall, the tool-call cap. Every
:class:`ToolCallRecord` is sorted here into

- **done** — a write that came back with a result;
- **refused** — a call the service layer rejected (its reason attached);
- **looked up** — a read that came back with a result;
- **not run** — a call that never reached the service layer: schema-invalid
  arguments, an unknown tool, or one the deadline, a Stop or the tool-call
  cap kept from starting.

and rendered with an explicit "Done: nothing." when nothing was. A limit stop
adds :data:`LIMIT_NOTE`: whatever the record does not show as done was not
done. Earlier runs' records reach the next run's prompts through
:mod:`app.ai.recall`.

Borrowed from chess's typed handoff (``chessapp/handoff.py``) and its budget
stop (``_BUDGET_NOTE`` / ``_budget_stop`` in ``llama_brain.py``). Pure: no
session, no I/O, nothing here reads the model's words.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from app.tools import registry
from app.tools.registry import UnknownToolError

if TYPE_CHECKING:
    from app.ai.loop import ToolCallRecord

# The tools that only read, from the registry's ``read_only`` marking. A tool
# added without the marking counts as a write, so it fails towards "done",
# which the user can see and undo, rather than towards "looked up", which
# would then read as nothing changed.
READ_TOOLS = registry.read_only_tool_names()

Outcome = Literal["done", "refused", "looked_up", "not_run"]

# The prefix of every error text the loop writes for a call it never
# dispatched (deadline, Stop, tool-call cap). The loop builds those texts from
# it, so a reworded one cannot silently start reading as a refusal.
NOT_RUN_PREFIX = "not run: "
# The loop's prefix for schema-invalid arguments (``AgentLoop._dispatch``).
INVALID_ARGUMENTS_PREFIX = "Invalid arguments: "

# How much of one call's arguments or answer the record carries. The record is
# a brief, not a transcript: enough to name what was touched.
_MAX_FIELD_CHARS = 160

# What the narrator is shown of a write's answer (the row it changed, trimmed)
# and of every read's answers together: enough to answer "what's due this
# week?" from, bounded so 24 reads cannot fill the window.
WRITE_ANSWER_CHARS = 400
READ_ANSWERS_MAX_CHARS = 8000

# The note under the record — chess's ``_BUDGET_NOTE``, worded for PCC.
LIMIT_NOTE = (
    "This run stopped before everything the user asked for was done. "
    "Whatever the record above does not show as done was not done."
)

# Why the run stopped, in words the narrator can pass on. Keyed by stop reason;
# these are exactly the limit stops the narrator speaks after.
LIMIT_REASONS: dict[str, str] = {
    "max_iterations": "it ran out of steps",
    "correction_limit": "it kept making malformed tool calls",
    "truncated": "its reply ran past the length limit",
    "no_progress": "it kept repeating the same lookups",
    "tool_call_limit": "it reached its limit of tool calls for one request",
}

def classify(record: ToolCallRecord) -> Outcome:
    """Sort one call by what its record shows happened."""
    if record.result is not None:
        return "looked_up" if record.tool in READ_TOOLS else "done"
    error = record.error or ""
    if error.startswith(NOT_RUN_PREFIX) or error.startswith(INVALID_ARGUMENTS_PREFIX):
        return "not_run"
    try:
        registry.get_tool(record.tool)
    except UnknownToolError:
        return "not_run"
    return "refused"


def any_ran(records: Sequence[ToolCallRecord]) -> bool:
    """Whether any call reached the service layer (done, refused or looked up).

    A run whose every call was malformed or never started has nothing verified
    to report, and keeps the plain stop line.
    """
    return any(classify(record) != "not_run" for record in records)


def render(
    records: Sequence[ToolCallRecord], answers: Sequence[str | None] | None = None
) -> str:
    """The record as the narrator reads it, grouped, one line per call.

    ``#n`` is the call's 1-based position in the run, so lines in different
    groups still read in order. ``answers``, aligned with ``records``, is what
    each call answered as the planner read it (the trimmed view, #414): given,
    a write shows its answer and a read shows what it found — the narrator
    answers a question from it — under :data:`READ_ANSWERS_MAX_CHARS` for all
    reads together; past that a read shows only how many rows it found.
    Without it a write shows its id and title and a read nothing.
    """
    groups: dict[Outcome, list[str]] = {
        "done": [],
        "refused": [],
        "looked_up": [],
        "not_run": [],
    }
    read_budget = READ_ANSWERS_MAX_CHARS
    for ref, record in enumerate(records, start=1):
        outcome = classify(record)
        answer = answers[ref - 1] if answers is not None else None
        line = f"#{ref} {record.tool} {clip(_compact(record.arguments))}"
        if outcome == "done":
            line += f" -> {answer_brief(record.result or '') if answer is None else clip(answer, WRITE_ANSWER_CHARS)}"
        elif outcome == "looked_up" and answer is not None:
            if len(answer) <= read_budget:
                line += f" -> {answer}"
                read_budget -= len(answer)
            else:
                line += f" -> {answer_brief(answer)} (not shown in full)"
        elif outcome in ("refused", "not_run"):
            line += f" -> {clip(record.error or '')}"
        groups[outcome].append(line)
    lines = ["Done:"]
    lines += [f"- {line}" for line in groups["done"]] or ["- nothing"]
    for outcome, heading in (
        ("refused", "Refused"),
        ("looked_up", "Looked up"),
        ("not_run", "Not run"),
    ):
        if groups[outcome]:
            lines.append(f"{heading}:")
            lines += [f"- {line}" for line in groups[outcome]]
    return "\n".join(lines)


def _compact(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, separators=(",", ":"), sort_keys=True, default=str)


def clip(text: str, limit: int = _MAX_FIELD_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def answer_brief(result: str) -> str:
    """What a write answered, cut to what names it: id plus title or name.

    Tool answers are the API read models (or a sentence for trash/remove), so
    an object with an ``id`` reduces to that and its label; anything else is
    clipped text.
    """
    try:
        value = json.loads(result)
    except ValueError:
        return clip(result)
    if isinstance(value, dict):
        return clip(_label(value) or json.dumps(value, default=str))
    if isinstance(value, list):
        labels = [_label(item) for item in value if isinstance(item, dict)]
        named = [label for label in labels if label]
        return clip(f"{len(value)} items" + (f": {', '.join(named)}" if named else ""))
    return clip(result)


def _label(value: dict[str, Any]) -> str:
    """``id 12 "Buy milk"`` for a read model; a nested ``project`` is followed."""
    if "id" not in value and isinstance(value.get("project"), dict):
        return _label(value["project"])
    if "id" not in value:
        return ""
    title = value.get("title") or value.get("name")
    return f"id {value['id']}" + (f' "{title}"' if title else "")
