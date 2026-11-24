"""Done tasks stay out of a group delete (#550).

"Delete the test tasks" means the open ones (the planner's group rule, #548).
The rule holds on a fresh request, and not when the search result reaches the
planner as history: said again in answer to an open pick-one question, it
trashed the first five candidates of the earlier search, three of them done.

So code holds the line the prompt drew. When one model turn of a gated run
asks to trash several tasks, the calls on tasks whose ``workflow_status`` is
``done`` are answered "not run" and never asked about, unless the person's
words ask for done tasks. One task named on its own is never held back:
"delete the task Test the docker container" trashes a done task as before.

The loop calls :func:`spared` where it arms the confirmations, so a call the
thread was already asked about (the app's own yes) never comes here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from app.ai import run_record
from app.db.models import Task, TaskWorkflowStatus
from app.tools.runtime import tool_session

# The one gated tool whose rows have a workflow status.
TOOL = "trash_task"

# Short enough for the record to carry whole (``run_record`` clips a reason).
NOT_RUN_ERROR = (
    f"{run_record.NOT_RUN_PREFIX}this task is done, and a request about several tasks "
    "leaves done ones alone unless it asks for them"
)

_ASKS_FOR_DONE = re.compile(r"\b(done|finished|completed)\b", re.IGNORECASE)


def asks_for_done(*words: str | None) -> bool:
    """Whether any of the person's ``words`` name done tasks."""
    return any(_ASKS_FOR_DONE.search(text) for text in words if text)


def spared(calls: Sequence[tuple[str, dict[str, Any]]], *words: str | None) -> set[int]:
    """Which of one turn's ``calls`` (by position) are held back: trashes of
    done tasks, when the turn trashes several tasks and ``words`` (the
    message, and the request an open question is about) don't ask for done
    ones. Empty when nothing is."""
    targets: dict[int, int] = {}
    for position, (tool, arguments) in enumerate(calls):
        task_id = arguments.get("task_id")
        if tool == TOOL and isinstance(task_id, int):
            targets[position] = task_id
    ids = set(targets.values())
    if len(ids) < 2 or asks_for_done(*words):
        return set()
    with tool_session("done_guard", write=False) as db:
        done = set(
            db.scalars(
                select(Task.id).where(
                    Task.id.in_(ids), Task.workflow_status == TaskWorkflowStatus.done
                )
            )
        )
    return {position for position, task_id in targets.items() if task_id in done}
