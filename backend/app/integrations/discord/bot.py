from __future__ import annotations

import sys

import discord
import structlog
from discord.ext import commands

from app.config import get_settings
from app.integrations.discord import commands as inbox_commands
from app.logging_config import configure_logging

logger = structlog.get_logger(__name__)


def build_bot() -> commands.Bot:
    """Create the bot with the /inbox command registered.

    Slash commands don't need the privileged message-content intent, so the
    default intents are enough.
    """
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    inbox_commands.register(bot)

    guild_id = get_settings().discord_guild_id

    @bot.event
    async def on_ready() -> None:
        # Sync slash commands with Discord so /inbox shows up. A guild-scoped sync
        # is instant (good for testing); a global sync can take up to ~an hour.
        if guild_id is not None:
            guild = discord.Object(id=guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        logger.info(
            "discord_bot_ready",
            user=str(bot.user),
            guild_id=guild_id,
            commands=[c.name for c in synced],
        )

    return bot


def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.discord_bot_token:
        logger.error("discord_bot_token_missing")
        sys.exit("DISCORD_BOT_TOKEN is not set — nothing to run.")
    build_bot().run(settings.discord_bot_token)


if __name__ == "__main__":
    main()
