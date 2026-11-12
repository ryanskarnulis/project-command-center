"""The code-built closing note for agent callers (#516).

``agent-standard/delegate-api.md`` "Agent callers and the closing note
(v1.1)": an agent caller reads the typed record, not PCC's prose, so the
planner's closing turn (the tool-free call that writes its note) is spent on
words nobody reads. A delegate run may end without it and reply with a note
code builds from the record (:func:`note`).

**When the run may end without it.** Only the closing turn may go: the run's
tool calls, limits and stop reason must be what they would have been with it.
The planner says it is done only *in* that turn, so code decides from what it
has, and the planner writes one call per turn whenever the next call needs
the last one's id (a probe on gemma-4-12b, 2026-11-12: "add a task … and pin
it", "a project Garden with two tasks", "a task with a subtask", "a task
blocked by …" each took one write turn per call, 4/4 twice; "complete X and
add Y" was one turn once, two the next). A landed write alone never says the
work is done. So the loop ends after a turn only when all of these hold
(``AgentLoop._run``):

- the caller is a delegate (``loop.DELEGATE_ACTORS``);
- the ask names one change (:func:`one_change`): one line, one sentence, no
  joiner and no word for a second change, so any ask that could need a
  second write keeps its closing turn;
- the turn made exactly one call, a write that landed (``done``), and it is
  the run's only write (:func:`ends_the_run`);
- turns are left, no Stop and no deadline yet: a limit, a Stop or a timeout
  ends the run as it would have with the note.

Everything else keeps today's path: a human caller's closing turn and
narrator, and a delegate's handoff (``narration.handoff``) after its closing
turn; a ``confirm_required`` question, a failure body, a limit stop.

Pure: builds text from stored tool calls, no session, no I/O.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from app.ai import receipts, run_record

if TYPE_CHECKING:
    from app.ai.loop import ToolCallRecord

# What makes an ask more than one change: a second line or sentence, a
# separator, a joining word, a plural target, or a word naming a change that
# rides on another (a pin, a dependency, a subtask, something new to file it
# in). Errs towards "more": a false "more" costs one model call, today's path;
# a false "one" would end a run before its second write.
_MORE_THAN_ONE = re.compile(
    r"""
    [\n,;:&+] | [.!?] \s+ \S
    | \b(?: and | then | also | plus | with | after | before | while | or | but
          | both | each | every | all | them | new | tasks | projects | subtasks?
          | pin\w* | focus | block\w* | depend\w* | parent )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def one_change(ask: str) -> bool:
    """Whether the ask names one change and nothing that could need a second."""
    return _MORE_THAN_ONE.search(ask.strip()) is None


def ends_the_run(records: Sequence[ToolCallRecord], turn_start: int) -> bool:
    """Whether the turn whose calls start at ``turn_start`` was one landed
    write, and the run's only one."""
    turn = records[turn_start:]
    if len(turn) != 1 or run_record.classify(turn[0]) != "done":
        return False
    return sum(1 for record in records if run_record.classify(record) == "done") == 1


# The verb for a write, by its tool's first word.
_VERBS = {
    "create": "Created",
    "update": "Updated",
    "complete": "Completed",
    "reopen": "Reopened",
    "trash": "Trashed",
    "restore": "Restored",
    "close": "Closed",
    "add": "Added",
    "remove": "Removed",
    "pin": "Pinned",
    "skip": "Skipped",
    "stop": "Stopped",
    "edit": "Edited",
    "reorder": "Reordered",
}
# What a project's trash answer says went with it (#520): ``and its 3 tasks``.
# Read from the tool's answer, not the record: the record's ``count`` is the
# rows its refs are of one kind with, and a client names a count by the kind
# of its first ref (conductor's narrator would say "4 projects").
_WENT_WITH = re.compile(r"\band its (\d+) tasks?\b")
# The arguments that name a row rather than change it.
_NAMING = frozenset({"task_id", "project_id", "entry_id", "series_id", "dependency_id"})
# Refs one sentence names before "and N more".
_NAMED = 3
_VALUE_CHARS = 40


def note(tool_calls: Sequence[Mapping[str, Any]]) -> str:
    """The note for a run's calls (stored ``tool_calls`` dicts): one sentence
    per write that landed, with its verb and what the typed record says it
    touched (kind, id and stored title, verbatim) or how many, plus what its
    answer says went with it (a project's tasks); and one per call the
    service layer refused, with its reason. Reads are left out; the record
    lists them."""
    sentences: list[str] = []
    for call, entry in zip(tool_calls, receipts.build(tool_calls), strict=True):
        if entry.outcome == "done":
            sentences.append(_done(call, entry))
        elif entry.outcome == "refused":
            reason = (entry.reason or "no reason given").rstrip(" .")
            sentences.append(f"Refused {entry.tool}: {reason}.")
    return " ".join(sentences) or "Nothing was changed."


def _done(call: Mapping[str, Any], entry: receipts.RecordEntry) -> str:
    verb_word, _, noun = entry.tool.partition("_")
    verb = _VERBS.get(verb_word, verb_word.capitalize())
    refs = entry.touched or []
    if not refs:
        return f"{verb} {noun.replace('_', ' ') or 'it'}."
    named = ", ".join(_ref(ref) for ref in refs[:_NAMED])
    said = f"{verb} {named}"
    rest = (entry.count or len(refs)) - min(len(refs), _NAMED)
    if rest > 0:
        said += f" and {rest} more"
    went = _went_with(call)
    if went:
        said += f" and its {went} {'task' if went == 1 else 'tasks'}"
    return said + _details(call, entry) + "."


def _went_with(call: Mapping[str, Any]) -> int:
    """How many tasks a project's trash or restore answer says went with it:
    the trash sentence's ``and its 3 tasks``, a restore's
    ``restored_task_count``."""
    result = call.get("result") or ""
    try:
        answer = json.loads(result)
    except ValueError:
        match = _WENT_WITH.search(result) if call.get("tool") == "trash_project" else None
        return int(match.group(1)) if match else 0
    if call.get("tool") == "restore_project" and isinstance(answer, dict):
        count = answer.get("restored_task_count")
        return count if isinstance(count, int) and not isinstance(count, bool) else 0
    return 0


def _ref(ref: receipts.EntityRef) -> str:
    kind = ref.kind.replace("_", " ")
    return f'{kind} {ref.id} "{ref.title}"' if ref.title is not None else f"{kind} {ref.id}"


def _details(call: Mapping[str, Any], entry: receipts.RecordEntry) -> str:
    """What a task write set, as its answer shows it: a create's due date; an
    update's changed fields (the title is already named)."""
    try:
        answer = json.loads(call.get("result") or "")
    except ValueError:
        return ""
    if not isinstance(answer, dict):
        return ""
    if entry.tool == "create_task":
        due = answer.get("due_date")
        return f", due {due}" if due else ""
    if entry.tool.startswith("update_"):
        changed = [
            key
            for key in (call.get("arguments") or {})
            if key not in _NAMING and key not in ("title", "name", "edit_scope")
        ]
        shown = [
            f"{key} {run_record.clip(str(answer.get(key, call['arguments'][key])), _VALUE_CHARS)}"
            for key in changed
        ]
        return f": {', '.join(shown)}" if shown else ""
    return ""
