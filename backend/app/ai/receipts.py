"""The typed record a delegate reply carries (agent-standard ``delegate-api.md`` v1.1).

The same code record the narrator and the handoff speak from
(:mod:`app.ai.run_record`), as data instead of prose: one entry per tool call,
with its outcome, the rows it touched as entity refs ``{app, kind, id,
title}``, and how to undo a write. Conductor speaks from these receipts, so
nothing here reads the model's words: entries come from the call's tool name,
arguments and result alone.

Pure: no session, no I/O. Built on the fly from stored ``tool_calls``, so
history reads carry the record too, and turns written before v1.1 get one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.ai import run_record

# The manifest ``name`` (app.yaml) that every ref names.
APP = "tasks"

# Refs one entry lists; ``count`` says how many there were in all.
MAX_TOUCHED = 20

# A write's default kind, by the noun its tool acts on.
_WRITE_KINDS: dict[str, str] = {
    "task": "task",
    "occurrence": "task",
    "recurrence": "task",
    "project": "project",
    "dependency": "dependency",
    "focus": "focus_entry",
}

# How to undo each write: the inverse tool and the argument it takes, from the
# call's arguments or its answer, and what to offer the user. A write missing
# here has no automatic undo (``test_every_write_has_an_undo_rule`` keeps the
# table complete).
_UNDO: dict[str, tuple[str, str, str] | str] = {
    "create_task": ("trash_task", "id", "move it to the trash"),
    "update_task": "an edit isn't versioned; change it back by hand",
    "complete_task": ("reopen_task", "task_id", "reopen it"),
    "reopen_task": ("complete_task", "task_id", "mark it done again"),
    "trash_task": ("restore_task", "task_id", "restore it and its subtasks from the trash"),
    "restore_task": ("trash_task", "task_id", "move it back to the trash"),
    "add_dependency": ("remove_dependency", "id", "remove the dependency"),
    "remove_dependency": "add the dependency again",
    "skip_occurrence": "a skipped occurrence can't be brought back automatically",
    "stop_recurrence": "set the repeat again by hand",
    "create_project": ("trash_project", "id", "move it to the trash"),
    "update_project": "an edit isn't versioned; change it back by hand",
    "close_project": ("reopen_project", "project_id", "reopen it"),
    "reopen_project": ("close_project", "project_id", "close it again"),
    "trash_project": ("restore_project", "project_id", "restore it and its tasks from the trash"),
    "restore_project": ("trash_project", "project_id", "move it back to the trash"),
    "pin_focus_task": ("remove_focus_entry", "id", "take it off the Focus plan"),
    "add_focus_commitment": ("remove_focus_entry", "id", "take it off the Focus plan"),
    "skip_focus_occurrence": "add the occurrence back by hand",
    "stop_focus_series": "start the series again by hand",
    "edit_focus_occurrence": "an edit isn't versioned; change it back by hand",
    "reorder_focus_pins": "reorder the pins again",
    "remove_focus_entry": ("restore_focus_entry", "entry_id", "put it back on the Focus plan"),
    "restore_focus_entry": ("remove_focus_entry", "entry_id", "take it off the Focus plan"),
}

# A sentence answer names its row as ``Task 41 "Pay rent"``. Read to the last
# quote, so a title that holds straight double quotes itself (#522) is whole.
_SENTENCE_TITLE = re.compile(r'"(.*)"')

Outcome = Literal["done", "refused", "looked_up", "not_run"]


class EntityRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    app: str = APP
    kind: str
    id: str
    title: str | None


class Undo(BaseModel):
    handle: str | None = None
    how: str | None = None
    none: str | None = None


class RecordEntry(BaseModel):
    """One tool call's receipt. Unset fields are left out on the wire."""

    tool: str
    outcome: Outcome
    touched: list[EntityRef] | None = None
    count: int | None = None
    reason: str | None = None
    undo: Undo | None = None


class _Call:
    """The three fields :func:`run_record.classify` reads, from a stored dict."""

    def __init__(self, call: Mapping[str, Any]) -> None:
        self.tool: str = str(call.get("tool", ""))
        self.arguments: dict[str, Any] = dict(call.get("arguments") or {})
        self.result: str | None = call.get("result")
        self.error: str | None = call.get("error")


def build(tool_calls: Sequence[Mapping[str, Any]] | None) -> list[RecordEntry]:
    """The record for one assistant turn's stored ``tool_calls``; ``[]`` for none."""
    return [entry(_Call(call)) for call in tool_calls or []]


def dump(entries: Sequence[RecordEntry]) -> list[dict[str, Any]]:
    """The wire form: unset fields left out, as ``delegate-api.md`` shows them."""
    return [e.model_dump(exclude_none=True) for e in entries]


def entry(call: _Call) -> RecordEntry:
    outcome = run_record.classify(call)  # type: ignore[arg-type]
    if outcome in ("refused", "not_run"):
        return RecordEntry(
            tool=call.tool, outcome=outcome, reason=run_record.clip(call.error or "")
        )
    refs = _touched(call, write=outcome == "done")
    record = RecordEntry(
        tool=call.tool,
        outcome=outcome,
        touched=refs[:MAX_TOUCHED],
        count=len(refs) if len(refs) > MAX_TOUCHED else None,
    )
    total = _total(call.result)
    if total is not None and total > len(record.touched or []):
        record.count = total
    if outcome == "done":
        record.undo = _undo(call)
    return record


def _touched(call: _Call, *, write: bool) -> list[EntityRef]:
    default = _default_kind(call.tool)
    try:
        value = json.loads(call.result or "")
    except ValueError:
        # A sentence answer (trash, remove): the row is named by the call's id
        # argument and the answer's quoted title.
        ident = _id_argument(call.arguments)
        if not write or ident is None:
            return []
        match = _SENTENCE_TITLE.search(call.result or "")
        return [EntityRef(kind=default, id=str(ident), title=match.group(1) if match else None)]
    refs: list[EntityRef] = []
    seen: set[tuple[str, str]] = set()

    def walk(node: Any, kind: str) -> None:
        if isinstance(node, dict):
            ref = _ref(node, kind)
            if ref is not None:
                if (ref.kind, ref.id) not in seen:
                    seen.add((ref.kind, ref.id))
                    refs.append(ref)
                return
            for key, child in node.items():
                walk(child, _KEY_KINDS.get(key, kind))
        elif isinstance(node, list):
            for child in node:
                walk(child, kind)

    walk(value, default)
    return refs


# A container key names the kind of the rows under it.
_KEY_KINDS = {"tasks": "task", "projects": "project", "project": "project"}


def _ref(node: Mapping[str, Any], kind: str) -> EntityRef | None:
    """A ref for a read model with an id and a display name, else ``None``."""
    if "id" not in node:
        return None
    if "depends_on_task_id" in node:
        return EntityRef(kind="dependency", id=str(node["id"]), title=node.get("depends_on_title"))
    if node.get("kind") in ("task", "project"):  # a search match names its kind
        kind = str(node["kind"])
    elif node.get("workflow_status") is not None and "title" in node:
        kind = "task"
    elif "name" in node and "title" not in node:
        kind = "project"
    elif "plan_date" in node:
        kind = "focus_entry"
    title = node.get("title") or node.get("name")
    if title is None and "title" not in node and "name" not in node:
        return None
    return EntityRef(kind=kind, id=str(node["id"]), title=title)


def _default_kind(tool: str) -> str:
    for noun, kind in _WRITE_KINDS.items():
        if noun in tool:
            return kind
    return "task"


def _id_argument(arguments: Mapping[str, Any]) -> Any:
    for key in ("task_id", "project_id", "entry_id", "series_id", "dependency_id"):
        if arguments.get(key) is not None:
            return arguments[key]
    return None


def _total(result: str | None) -> int | None:
    """A paged read's ``total`` (``TaskPage``), when it says more than it lists."""
    try:
        value = json.loads(result or "")
    except ValueError:
        return None
    if isinstance(value, dict) and isinstance(value.get("total"), int):
        return int(value["total"])
    return None


def _undo(call: _Call) -> Undo:
    rule = _UNDO.get(call.tool)
    if rule is None:
        return Undo(none="no automatic undo; change it back by hand")
    if isinstance(rule, str):
        return Undo(none=rule)
    inverse, source, how = rule
    ident = call.arguments.get(source)
    if ident is None:
        try:
            answer = json.loads(call.result or "")
        except ValueError:
            answer = None
        if isinstance(answer, dict):
            ident = answer.get(source)
            if ident is None and isinstance(answer.get("project"), dict):
                ident = answer["project"].get(source)
    if ident is None:
        return Undo(none="the row's id wasn't in the answer; undo it by hand")
    return Undo(handle=f"{inverse}:{ident}", how=how)
