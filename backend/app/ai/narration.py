"""The narrator: the one phase that speaks to the user (#418).

A run has two model phases (``docs/agent-design.md`` "Planner and
narrator"), ported from chess's planner/narrator split (chess
``docs/planner-narrator.md``):

- **the planner** is the bounded tool loop in :mod:`app.ai.loop`, on a short
  contract with no persona. Its last, tool-free turn is a handoff note for
  this phase; the user never sees it unless this phase cannot speak.
- **the narrator** is one further call, on :data:`NARRATOR_PROMPT` (a
  speaking contract plus global Glitch), offered **no tools**, and handed a
  brief (:func:`messages`): what came before the run (:mod:`app.ai.recall`),
  the user's words, the run's record sorted by code (:mod:`app.ai.run_record`)
  with what each call answered, the gathered matches, and the planner's note
  labelled as a reading. Its text is the reply. Being unable to act is what
  enforces "say only what the record shows".

There is one narrator for every way a run ends that has something to say: a
completed run, and a limit stop where a call reached the service layer
(#399's summary, folded in here as chess#369 folded its two narrators). A
limit stop's brief adds why it stopped and :data:`run_record.LIMIT_NOTE`.

Pure: builds the request, makes no call.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Any

from app.ai import run_record, state_block

if TYPE_CHECKING:
    from app.ai.loop import ToolCallRecord

# The narrator's contract — a speaking contract. It holds no tools and acts on
# nothing; by the time it speaks, what was done is settled and handed to it as
# a record. So its rules are about what it may *say*. The tools-only and
# id/date rules are the planner's; here they would be one more thing to answer
# instead of the user (chess #289).
NARRATOR_RULES = """\
You are the assistant for Project Command Center (PCC), the user's project and \
task manager, and the voice the user talks to. By the time you reply, whatever \
was done about the user's message is settled, and you are handed the record of \
it: your job is to say it. You cannot call tools.

Rules:
- Say only what the record shows. Never claim a task, project, change or id the \
record does not list under "Done", and never invent one.
- Something the user asked for that the record does not show as done was not \
done: say so plainly.
- Answer a question from what the record looked up, and from nothing else.
- When the user has to choose or answer something, ask, and name the options.
- Plain text. No tool-call syntax, no JSON."""

# Whatever the planner wrote when it stopped calling tools: its reading, never
# a record — a line it wrote can be wrong where the record cannot.
_NOTE_LABEL = "The planner's note (its reading of the results, not a record)"


def prompt(personality: str) -> str:
    """The narrator's system prompt: the contract, then global Glitch.
    Byte-identical across runs and days, so the server can cache it."""
    return f"{NARRATOR_RULES}\n\n{personality}"


def brief(
    *,
    user_message: str,
    earlier: str | None,
    records: Sequence[ToolCallRecord],
    answers: Sequence[str | None],
    note: str | None,
    gathered: str | None,
    stop_reason: str,
    today: date,
) -> str:
    """The narrator's one user message: the run handed over as data."""
    parts = [earlier] if earlier else []
    parts.append(f"Today is {state_block.day(today)}.")
    parts.append(f"The user said:\n{user_message}")
    parts.append(
        "The record of this run (built by the app from the tool results):\n"
        + run_record.render(records, answers)
    )
    if gathered:
        parts.append(gathered)
    if note:
        parts.append(f'{_NOTE_LABEL}: "{note.strip()}"')
    if stop_reason in run_record.LIMIT_REASONS:
        why = run_record.LIMIT_REASONS[stop_reason]
        parts.append(
            f"The run stopped because {why}. {run_record.LIMIT_NOTE}\n"
            "Tell the user what was done and that the rest was not; they can ask again."
        )
    else:
        parts.append("Now reply to the user.")
    return "\n\n".join(parts)


def messages(system_prompt: str, brief_text: str) -> list[dict[str, Any]]:
    """The whole narrator request: its own system prompt and the brief."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": brief_text},
    ]
