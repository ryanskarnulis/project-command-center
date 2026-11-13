"""The code-built closing note for agent callers (#516).

``agent-standard/delegate-api.md`` "Agent callers and the closing note
(v1.1)": an agent caller reads the typed record, not PCC's prose, so the
planner's closing turn (the tool-free call that writes its note) is spent on
words nobody reads. Its generation is saved; the call itself is not skipped.

**Only the closing turn may go**: the run's tool calls, limits and stop
reason must be what they would have been with it, and the planner says it
is done only *in* that turn. It writes one call per turn whenever the next
call needs the last one's id (a probe on gemma-4-12b, 2026-11-12: a project
then its tasks, a task then its pin, its subtask or its blocker), so a
landed write alone never says the work is done. So the call is peeked, as
music does (music#97, its ``peek``):

- after a tool turn that **settled** (:func:`settled`: every call in it a
  write that landed, ``done``), a delegate's next planner call is made
  exactly as always, after every check the loop makes before a call (the
  Stop, the deadline, the iteration bound; the stall rule and the caps ran
  at the end of the turn), but streamed through ``provider.peek``;
- a first meaningful delta that is a tool call is read whole and the run goes
  on as it would have;
- a first meaningful delta of plain content (or an empty text turn) is the
  model beginning its closing line: the stream is stopped there and the
  reply is :func:`note`, built from the typed record.

A read, a refusal, a not-run or malformed call never arm the peek: the
model's next words may answer, explain or ask, and they stay its own. So do a
``confirm_required`` question, an ``ask_user`` question, a failure body and a
limit stop, and a human caller is unchanged. A provider with no ``peek`` (a
scripted one) makes the plain call.

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


def settled(records: Sequence[ToolCallRecord], turn_start: int) -> bool:
    """Whether the turn whose calls start at ``turn_start`` made at least one
    call and every one of them is a write that landed."""
    turn = records[turn_start:]
    return bool(turn) and all(run_record.classify(record) == "done" for record in turn)


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
