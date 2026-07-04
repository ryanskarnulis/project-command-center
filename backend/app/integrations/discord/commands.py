from __future__ import annotations

from typing import Any

import httpx
import structlog
from discord import AllowedMentions, Interaction, app_commands
from discord.ext import commands

from app.config import get_settings

logger = structlog.get_logger(__name__)
NO_MENTIONS = AllowedMentions.none()

# How many tasks the bot lists before collapsing the rest into an "…and N more" line.
_LIST_CAP = 10


class BackendError(Exception):
    """A backend call failed; the message is safe to show the Discord user."""


# Kept as an alias so existing imports/tests referencing InboxError still resolve.
InboxError = BackendError


def _raise_for_status(resp: httpx.Response, *, extra: dict[int, str] | None = None) -> None:
    """Map a non-2xx backend response onto a user-safe ``BackendError``.

    Common auth/config statuses get friendly text; ``extra`` lets a caller add
    endpoint-specific messages (e.g. 409 on mark-done). Any other ≥400 is logged
    (with the body, never shown) and surfaced as a generic error — the secret and
    stack traces never reach Discord.
    """
    if resp.status_code < 400:
        return
    if extra and resp.status_code in extra:
        raise BackendError(extra[resp.status_code])
    if resp.status_code == 401:
        raise BackendError("Backend rejected the shared secret (check BACKEND_SHARED_SECRET).")
    if resp.status_code == 503:
        raise BackendError("Discord integration isn't configured on the backend yet.")
    if resp.status_code == 422:
        raise BackendError("The model returned something unparseable — nothing was captured.")
    logger.error("discord_backend_error", status=resp.status_code, body=resp.text)
    raise BackendError(f"Backend error ({resp.status_code}).")


async def post_inbox(raw_text: str) -> dict[str, Any]:
    """POST inbox text to the backend's discord route and return the parsed body.

    Raises ``BackendError`` with a user-facing message on any non-2xx response or
    network failure — never the secret or a raw stack trace.
    """
    settings = get_settings()
    url = f"{settings.backend_base_url}/api/discord/inbox"
    headers = {"X-Backend-Secret": settings.backend_shared_secret}
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.post(url, json={"raw_text": raw_text}, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("discord_backend_unreachable", error=str(exc))
        raise BackendError("Couldn't reach the backend. Is it running?") from exc

    _raise_for_status(resp)
    body: dict[str, Any] = resp.json()
    return body


async def get_tasks(project: str | None) -> dict[str, Any]:
    """GET open tasks, optionally filtered to one project by name/alias."""
    settings = get_settings()
    url = f"{settings.backend_base_url}/api/discord/tasks"
    headers = {"X-Backend-Secret": settings.backend_shared_secret}
    params = {"project": project} if project else None
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("discord_backend_unreachable", error=str(exc))
        raise BackendError("Couldn't reach the backend. Is it running?") from exc

    _raise_for_status(resp)
    body: dict[str, Any] = resp.json()
    return body


async def search_tasks(query: str) -> list[dict[str, Any]]:
    """GET ranked open-task candidates matching ``query`` (for ``/done``)."""
    settings = get_settings()
    url = f"{settings.backend_base_url}/api/discord/tasks/search"
    headers = {"X-Backend-Secret": settings.backend_shared_secret}
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url, params={"q": query}, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("discord_backend_unreachable", error=str(exc))
        raise BackendError("Couldn't reach the backend. Is it running?") from exc

    _raise_for_status(resp)
    tasks: list[dict[str, Any]] = resp.json().get("tasks") or []
    return tasks


async def mark_done(task_id: int) -> None:
    """Complete a task via the recurrence-preserving endpoint.

    A 409 means the task can't be completed directly (blocked, or a checklist
    parent whose status is derived from its subtasks) — surfaced as a friendly
    message rather than a raw error.
    """
    settings = get_settings()
    url = f"{settings.backend_base_url}/api/tasks/{task_id}/done"
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(url)
    except httpx.HTTPError as exc:
        logger.error("discord_backend_unreachable", error=str(exc))
        raise BackendError("Couldn't reach the backend. Is it running?") from exc

    _raise_for_status(
        resp,
        extra={
            404: "That task no longer exists.",
            409: "Can't complete it — it's blocked, or its status comes from its subtasks.",
        },
    )


def _task_line(index: int, task: dict[str, Any]) -> str:
    """Render one numbered task line: title, project, and due date when present."""
    parts = [f"**{index}.** {task['title']}"]
    if task.get("project_name"):
        parts.append(f"· {task['project_name']}")
    if task.get("due_date"):
        parts.append(f"· due {task['due_date']}")
    return " ".join(parts)


def format_task_list(data: dict[str, Any]) -> str:
    """Render ``/tasks`` output: a numbered list capped at ``_LIST_CAP`` lines."""
    tasks: list[dict[str, Any]] = data.get("tasks") or []
    total: int = data.get("total", len(tasks))
    if not tasks:
        return "No open tasks. 🎉"

    shown = tasks[:_LIST_CAP]
    lines = [f"**{total} open task(s):**"]
    lines.extend(_task_line(i, t) for i, t in enumerate(shown, start=1))
    if total > len(shown):
        lines.append(f"_…and {total - len(shown)} more — see the web app._")
    return "\n".join(lines)


def format_disambiguation(tasks: list[dict[str, Any]]) -> str:
    """Render the ``/done`` multi-match reply. No task is completed here."""
    lines = ["Multiple tasks match — narrow it down and try again:"]
    lines.extend(_task_line(i, t) for i, t in enumerate(tasks[:_LIST_CAP], start=1))
    return "\n".join(lines)


def register(bot: commands.Bot) -> None:
    """Attach the /inbox, /tasks, and /done slash commands to the command tree."""

    @bot.tree.command(name="inbox", description="Capture messy text as task candidates")
    @app_commands.describe(text="The note to extract tasks from")
    async def inbox(interaction: Interaction, text: str) -> None:
        # Extraction can take a while; defer so the interaction doesn't time out.
        await interaction.response.defer(thinking=True)
        try:
            data = await post_inbox(text)
        except BackendError as exc:
            await interaction.followup.send(f"⚠️ {exc}", allowed_mentions=NO_MENTIONS)
            return
        await interaction.followup.send(format_reply(data), allowed_mentions=NO_MENTIONS)

    @bot.tree.command(name="tasks", description="List open tasks, optionally by project")
    @app_commands.describe(project="Optional project name or alias to filter by")
    async def tasks(interaction: Interaction, project: str | None = None) -> None:
        await interaction.response.defer(thinking=True)
        try:
            data = await get_tasks(project)
        except BackendError as exc:
            await interaction.followup.send(f"⚠️ {exc}", allowed_mentions=NO_MENTIONS)
            return
        await interaction.followup.send(
            format_task_list(data), allowed_mentions=NO_MENTIONS
        )

    @bot.tree.command(name="done", description="Mark an open task done by searching its title")
    @app_commands.describe(query="Part of the task title to complete")
    async def done(interaction: Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            candidates = await search_tasks(query)
        except BackendError as exc:
            await interaction.followup.send(f"⚠️ {exc}", allowed_mentions=NO_MENTIONS)
            return

        if not candidates:
            await interaction.followup.send(
                f'No open task matches "{query}".', allowed_mentions=NO_MENTIONS
            )
            return
        if len(candidates) > 1:
            # Ambiguous: show the options and complete nothing.
            await interaction.followup.send(
                format_disambiguation(candidates), allowed_mentions=NO_MENTIONS
            )
            return

        task = candidates[0]
        try:
            await mark_done(task["id"])
        except BackendError as exc:
            await interaction.followup.send(f"⚠️ {exc}", allowed_mentions=NO_MENTIONS)
            return
        await interaction.followup.send(
            f"✅ Marked done: {task['title']}", allowed_mentions=NO_MENTIONS
        )


def format_reply(data: dict[str, Any]) -> str:
    """Render the /inbox backend response into a Discord message."""
    titles: list[str] = data.get("task_titles") or []
    lines: list[str] = []
    if data.get("summary"):
        lines.append(f"**Summary:** {data['summary']}")
    if data.get("project_hint"):
        lines.append(f"**Project hint:** {data['project_hint']}")

    if titles:
        lines.append(f"**Captured {len(titles)} task(s):**")
        lines.extend(f"• {title}" for title in titles)
    else:
        lines.append("No tasks extracted.")

    lines.append("_Review and accept these in the web app._")
    return "\n".join(lines)
