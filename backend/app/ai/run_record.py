"""The run record: what a stopped run did, built by code from its tool calls.

When the loop stops at a limit (#399) — out of turns, out of corrections, a
cut-off reply, a stall, the tool-call cap — the model never got to write its
closing reply. Rather than leave the user (or a spoken reply, or a conductor
delegate) with nothing, the loop makes one tool-free call that reads *this*
record and says what the run did. The record is the source of the claims, not
the model's earlier words: every :class:`ToolCallRecord` is sorted here into

- **done** — a write that came back with a result;
- **refused** — a call the service layer rejected (its reason attached);
- **looked up** — a read that came back with a result;
- **not run** — a call that never reached the service layer: schema-invalid
  arguments, an unknown tool, or one the deadline, a Stop or the tool-call
  cap kept from starting.

and rendered with an explicit "Done: nothing." when nothing was, followed by
:data:`LIMIT_NOTE`: whatever the record does not show as done was not done.

Borrowed from chess's typed handoff (``chessapp/handoff.py``) and its budget
stop (``_BUDGET_NOTE`` / ``_budget_stop`` in ``llama_brain.py``). Pure: no
session, no I/O, nothing here reads the model's words.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from app.tools import registry
from app.tools.registry import UnknownToolError

if TYPE_CHECKING:
    from app.ai.loop import ToolCallRecord

# The tools that only read — exactly the registry's ``tool_session(...,
# write=False)`` bodies, which a test pins. Named as the reads rather than the
# writes so a tool added later without a classification fails towards "done",
# which the user can see and undo, rather than towards "looked up", which the
# summary would then leave out.
READ_TOOLS = frozenset(
    {
        "list_tasks",
        "get_task",
        "list_dependencies",
        "list_projects",
        "get_project",
        "search",
        "get_focus_plan",
        "list_trash",
        "list_activity",
    }
)

Outcome = Literal["done", "refused", "looked_up", "not_run"]

# The prefix of every error text the loop writes for a call it never
# dispatched (deadline, Stop, tool-call cap). The loop builds those texts from
# it, so a reworded one cannot silently start reading as a refusal.
NOT_RUN_PREFIX = "not run: "
# The loop's prefix for schema-invalid arguments (``AgentLoop._dispatch``).
INVALID_ARGUMENTS_PREFIX = "Invalid arguments: "

# How much of one call's arguments or answer the record carries. The record is
# a summary input, not a transcript: enough to name what was touched.
_MAX_FIELD_CHARS = 160

# The note under the record — chess's ``_BUDGET_NOTE``, worded for PCC.
LIMIT_NOTE = (
    "This run stopped before everything the user asked for was done. "
    "Whatever the record above does not show as done was not done."
)

# Why the run stopped, in words the summary can pass on. Keyed by stop reason;
# these are exactly the limit stops the loop summarises.
LIMIT_REASONS: dict[str, str] = {
    "max_iterations": "it ran out of steps",
    "correction_limit": "it kept making malformed tool calls",
    "truncated": "its reply ran past the length limit",
    "no_progress": "it kept repeating the same lookups",
    "tool_call_limit": "it reached its limit of tool calls for one request",
}

# The summary call's own system prompt. Not the loop's: that one says "you act
# only by calling the provided tools", and this call is offered none.
_SUMMARY_RULES = """\
You are the assistant for Project Command Center (PCC), the user's project and \
task manager. Your run just stopped at a limit before it finished, and you now \
tell the user what happened. You cannot call tools now.

Rules:
- Say what was done, using only the record you are given. Never claim a task, \
project, change or id the record does not list under "Done".
- Say plainly that the rest was not done, and what is left, if the request makes \
that clear. The user can ask again to finish it.
- Plain text, short: one to three sentences. No tool-call syntax, no JSON."""


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


def render(records: Sequence[ToolCallRecord]) -> str:
    """The record as the summary call reads it, grouped, one line per call.

    ``#n`` is the call's 1-based position in the run, so lines in different
    groups still read in order.
    """
    groups: dict[Outcome, list[str]] = {
        "done": [],
        "refused": [],
        "looked_up": [],
        "not_run": [],
    }
    for ref, record in enumerate(records, start=1):
        outcome = classify(record)
        line = f"#{ref} {record.tool} {_clip(_compact(record.arguments))}"
        if outcome == "done":
            line += f" -> {_answer_brief(record.result or '')}"
        elif outcome in ("refused", "not_run"):
            line += f" -> {_clip(record.error or '')}"
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


def summary_messages(
    user_message: str,
    records: Sequence[ToolCallRecord],
    stop_reason: str,
    personality: str,
    today: date,
) -> list[dict[str, Any]]:
    """The whole request for the tool-free summary call: a fresh brief.

    Not the run's transcript: that is what ran out (of turns, corrections,
    room), and a model reading its own tool chatter is the one that might
    claim a write it only proposed. It reads the ask, the record and the note.
    """
    why = LIMIT_REASONS.get(stop_reason, "it hit a limit")
    brief = (
        f"The user asked:\n{user_message}\n\n"
        "The record of this run (built by the app from the tool results):\n"
        f"{render(records)}\n\n"
        f"The run stopped because {why}. {LIMIT_NOTE}\n\n"
        "Now tell the user what was done and that the rest was not."
    )
    return [
        {
            "role": "system",
            "content": (
                f"{_SUMMARY_RULES}\n\n{personality}\n\n"
                f"Today's date is {today.isoformat()}."
            ),
        },
        {"role": "user", "content": brief},
    ]


def _compact(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, separators=(",", ":"), sort_keys=True, default=str)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _MAX_FIELD_CHARS:
        return text
    return text[: _MAX_FIELD_CHARS - 1] + "…"


def _answer_brief(result: str) -> str:
    """What a write answered, cut to what names it: id plus title or name.

    Tool answers are the API read models (or a sentence for trash/remove), so
    an object with an ``id`` reduces to that and its label; anything else is
    clipped text.
    """
    try:
        value = json.loads(result)
    except ValueError:
        return _clip(result)
    if isinstance(value, dict):
        return _clip(_label(value) or json.dumps(value, default=str))
    if isinstance(value, list):
        labels = [_label(item) for item in value if isinstance(item, dict)]
        named = [label for label in labels if label]
        return _clip(f"{len(value)} items" + (f": {', '.join(named)}" if named else ""))
    return _clip(result)


def _label(value: dict[str, Any]) -> str:
    """``id 12 "Buy milk"`` for a read model; a nested ``project`` is followed."""
    if "id" not in value and isinstance(value.get("project"), dict):
        return _label(value["project"])
    if "id" not in value:
        return ""
    title = value.get("title") or value.get("name")
    return f"id {value['id']}" + (f' "{title}"' if title else "")
