from __future__ import annotations

from typing import Any

import httpx
import structlog
from discord import Interaction, app_commands
from discord.ext import commands

from app.config import get_settings

logger = structlog.get_logger(__name__)


class InboxError(Exception):
    """A backend call failed; the message is safe to show the Discord user."""


async def post_inbox(raw_text: str) -> dict[str, Any]:
    """POST inbox text to the backend's discord route and return the parsed body.

    Raises ``InboxError`` with a user-facing message on any non-2xx response or
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
        raise InboxError("Couldn't reach the backend. Is it running?") from exc

    if resp.status_code == 401:
        raise InboxError("Backend rejected the shared secret (check BACKEND_SHARED_SECRET).")
    if resp.status_code == 503:
        raise InboxError("Discord integration isn't configured on the backend yet.")
    if resp.status_code == 422:
        raise InboxError("The model returned something unparseable — nothing was captured.")
    if resp.status_code >= 400:
        logger.error("discord_backend_error", status=resp.status_code, body=resp.text)
        raise InboxError(f"Backend error ({resp.status_code}).")

    body: dict[str, Any] = resp.json()
    return body


def format_reply(data: dict[str, Any]) -> str:
    """Render the backend response into a Discord message."""
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


def register(bot: commands.Bot) -> None:
    """Attach the /inbox slash command to the bot's command tree."""

    @bot.tree.command(name="inbox", description="Capture messy text as task candidates")
    @app_commands.describe(text="The note to extract tasks from")
    async def inbox(interaction: Interaction, text: str) -> None:
        # Extraction can take a while; defer so the interaction doesn't time out.
        await interaction.response.defer(thinking=True)
        try:
            data = await post_inbox(text)
        except InboxError as exc:
            await interaction.followup.send(f"⚠️ {exc}")
            return
        await interaction.followup.send(format_reply(data))
