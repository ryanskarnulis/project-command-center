"""The state block: what the agent is shown about today and the data, every run (#412).

The only per-run context used to be ``Today's date is YYYY-MM-DD.`` in the
system prompt — no weekday, no picture of the data — so most tool-using runs
opened with a lookup (``list_projects → create_task`` the commonest pair), ids
were guessed, and "next Friday" landed on a Saturday. Chess fixed the same
class of miss with a code-built state block every turn (``planner_state``):
code does the arithmetic, the model reads the answer.

The block is built once at the start of a run and goes at the head of the
current user message — after the system prompt and tool schemas, which stay
byte-identical across runs and days so the server's prompt cache keeps them —
and is never saved into history: the conversation stores what the user typed,
and the next run gets a fresh block. It is labelled as of the start of the run,
so a reader knows the agent's own writes after that are not in it.

Building it never costs the run: a snapshot query that fails is logged and the
block falls back to the calendar alone.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import structlog

from app.services import dashboard
from app.services.dashboard import AgentSnapshot
from app.tools import runtime

logger = structlog.get_logger(__name__)

# Lanes listed by name and id; past this the rest is a count and a pointer to
# ``list_projects``, so a big roster can't crowd the window.
MAX_ROSTER = 25
# Project names are unbounded; one is cut to this many characters here so the
# block's size is bounded by these two numbers alone.
MAX_NAME_CHARS = 60

_HEADER = "[State at the start of this run — written by the app, not the user]"
_FOOTER = "[End of state]"


def day(when: date) -> str:
    """``Wednesday 2026-10-21``: the weekday is what date math needs."""
    return f"{calendar.day_name[when.weekday()]} {when.isoformat()}"


def calendar_lines(today: date) -> list[str]:
    """Today, tomorrow, the rest of this week (to Sunday), next Monday, and the
    end of the month — each with its weekday. The rest-of-week line is left
    out on Saturday and Sunday, when nothing past tomorrow is left in it."""
    tomorrow = today + timedelta(days=1)
    sunday = today + timedelta(days=6 - today.weekday())
    rest = [
        tomorrow + timedelta(days=offset)
        for offset in range(1, (sunday - tomorrow).days + 1)
    ]
    next_monday = sunday + timedelta(days=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    lines = [f"Today: {day(today)}", f"Tomorrow: {day(tomorrow)}"]
    if rest:
        lines.append("Rest of this week: " + ", ".join(day(when) for when in rest))
    lines.append(f"Next Monday: {day(next_monday)}")
    lines.append(f"End of this month: {day(month_end)}")
    return lines


def render(today: date, snapshot: AgentSnapshot | None) -> str:
    """The block's text. ``snapshot`` ``None`` (the query failed) leaves the
    calendar alone, rather than a roster that reads as "no projects"."""
    lines = [_HEADER, *calendar_lines(today)]
    if snapshot is not None:
        if snapshot.roster:
            lines.append("Open projects (id · name · open tasks · overdue):")
            for entry in snapshot.roster[:MAX_ROSTER]:
                lines.append(
                    f"- {entry.project_id} · {_clip(entry.name)} · {entry.open_count} open"
                    f" · {entry.overdue_count} overdue"
                )
            hidden = len(snapshot.roster) - MAX_ROSTER
            if hidden > 0:
                lines.append(f"- …and {hidden} more (list_projects has them all)")
        else:
            lines.append("Open projects: none yet.")
        lines.append(
            f"Open tasks: {snapshot.overdue} overdue, {snapshot.due_today} due today, "
            f"{snapshot.blocked} blocked."
        )
    lines.append(_FOOTER)
    return "\n".join(lines)


def _clip(name: str) -> str:
    flat = " ".join(name.split())
    return flat if len(flat) <= MAX_NAME_CHARS else flat[: MAX_NAME_CHARS - 1] + "…"


def build(today: date) -> str:
    """The block for a run starting ``today``, read on its own read-only session
    (the tools' factory, so tests' and evals' databases apply)."""
    snapshot: AgentSnapshot | None = None
    try:
        with runtime.session_factory() as db:
            snapshot = dashboard.get_agent_snapshot(db, today)
    except Exception as exc:
        logger.warning(
            "agent_state_block_failed", error=str(exc), error_type=type(exc).__name__
        )
    return render(today, snapshot)


def with_state(block: str, user_message: str) -> str:
    """The current user turn as the model sees it: the block, then the ask."""
    return f"{block}\n\n{user_message}"
