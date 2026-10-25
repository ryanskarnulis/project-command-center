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
  the state block, the user's words, the run's record sorted by code (:mod:`app.ai.run_record`)
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
from typing import TYPE_CHECKING, Any

from app.ai import run_record
from app.ai.clarification import Candidate

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
- Answer a question from what the record looked up or the state block shows, \
and from nothing else.
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
    state: str,
    asked: Sequence[Candidate] = (),
) -> str:
    """The narrator's one user message: the run handed over as data. ``state``
    is the state block the planner's run opened with (#412): the app's facts
    as of the run's start — the calendar, the projects' open counts — which
    the planner may have answered from without a tool. ``asked`` are the
    candidates of the ``ask_user`` the run ended on (#420): the reply asks
    which one the user meant, naming them in that order."""
    parts = [earlier] if earlier else []
    parts.append(state)
    parts.append(f"The user said:\n{user_message}")
    parts.append(
        "The record of this run (built by the app from the tool results):\n"
        + run_record.render(records, answers)
    )
    if gathered:
        parts.append(gathered)
    if note:
        parts.append(f'{_NOTE_LABEL}: "{note.strip()}"')
    if asked:
        named = "\n".join(
            f"{number}. {candidate.title}" for number, candidate in enumerate(asked, start=1)
        )
        parts.append(
            "The run ended on a question: more than one task fits what the user said, so "
            "nothing was changed for it. Ask which one they meant, naming every one of these "
            f"tasks by its title, in this order:\n{named}"
        )
    elif stop_reason in run_record.LIMIT_REASONS:
        why = run_record.LIMIT_REASONS[stop_reason]
        parts.append(
            f"The run stopped because {why}. {run_record.LIMIT_NOTE}\n"
            "Tell the user what was done and that the rest was not; they can ask again."
        )
    else:
        parts.append("Now reply to the user.")
    return "\n\n".join(parts)


# How a handoff opens. Its record is one block (``run_record.render`` joins
# its lines with single newlines), and every part after it is a paragraph.
HANDOFF_HEAD = (
    "PCC's record of this run (built by the app from its tool results; nothing "
    'outside "Done" was changed):\n'
)


def handoff(
    *,
    records: Sequence[ToolCallRecord],
    answers: Sequence[str | None],
    note: str | None,
    stop_reason: str,
    asked: Sequence[Candidate] = (),
) -> str:
    """The reply a delegate caller gets in place of the narrator's (#482).

    A delegate (conductor) speaks to the user in its own voice, so a Glitch
    reply would only be re-voiced: it gets the typed handoff instead — the
    run's record as the narrator reads it, the planner's note labelled as a
    reading, and, as the narrator would be told, why a limit stop stopped or
    the question an ``ask_user`` ended on. Built by code; no model call."""
    parts = [HANDOFF_HEAD + run_record.render(records, answers)]
    if note and note.strip():
        parts.append(f'{_NOTE_LABEL}: "{note.strip()}"')
    if asked:
        named = "\n".join(
            f"{number}. {candidate.title}" for number, candidate in enumerate(asked, start=1)
        )
        parts.append(
            "The run ended on a question: more than one task fits, so nothing was changed "
            f"for it. Ask the user which one they meant:\n{named}"
        )
    elif stop_reason in run_record.LIMIT_REASONS:
        why = run_record.LIMIT_REASONS[stop_reason]
        parts.append(f"The run stopped because {why}. {run_record.LIMIT_NOTE}")
    return "\n\n".join(parts)


def handoff_words(reply: str) -> str | None:
    """A stored reply as words for recall: a handoff without its record.

    A delegate's stored reply is its handoff (#482), and recall already keeps
    that record. Shown whole as the last reply, the next planner copied its
    format into its own note. What is left is what was written beside it: the
    note, the limit or the question. ``None`` when there is nothing. Any
    other reply comes back as it is."""
    if not reply.startswith(HANDOFF_HEAD):
        return reply
    _, _, words = reply.partition("\n\n")
    return words or None


def messages(system_prompt: str, brief_text: str) -> list[dict[str, Any]]:
    """The whole narrator request: its own system prompt and the brief."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": brief_text},
    ]
