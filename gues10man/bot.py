from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .config import Settings
from .db import Database
from .ranks import HenrikClient
from .util import reply

log = logging.getLogger("gues10man")

EXTENSIONS = ("gues10man.cogs.lobby", "gues10man.cogs.players", "gues10man.cogs.admin")


class TenManBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.voice_states = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.db = Database(settings.database_path)
        self.henrik = HenrikClient(settings.henrik_api_key)
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        await self.db.connect()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        if self.settings.guild_id:
            guild = discord.Object(self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self) -> None:
        await self.henrik.close()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await reply(interaction, str(error) or "You can't use this command.")
            return
        log.exception("Command error", exc_info=error)
        await reply(interaction, "Something went wrong running that command.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    bot = TenManBot(settings)
    bot.run(settings.token, log_handler=None)
