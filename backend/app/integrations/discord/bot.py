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

    @bot.event
    async def on_ready() -> None:
        # Sync slash commands with Discord so /inbox shows up.
        await bot.tree.sync()
        logger.info("discord_bot_ready", user=str(bot.user))

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
