from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from .config import Settings
from .db import Database
from .games import GAMES
from .ranks import HenrikClient
from .rating import refit
from .util import FAKE_PLAYERS, reply

log = logging.getLogger("gues10man")

EXTENSIONS = (
    "gues10man.cogs.lobby",
    "gues10man.cogs.players",
    "gues10man.cogs.admin",
    "gues10man.cogs.sandbox",
    "gues10man.cogs.importer",
)


class TenManBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.db = Database(settings.database_path)
        self.henrik = HenrikClient(settings.henrik_api_key)
        self.tree.on_error = self.on_app_command_error
        self.fit_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def setup_hook(self) -> None:
        await self.db.connect()
        FAKE_PLAYERS.update(await self.db.fake_players())
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        if not self.settings.guild_ids:
            await self.tree.sync()
            return
        refused = []
        for guild_id in self.settings.guild_ids:
            guild = discord.Object(guild_id)
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
            except discord.Forbidden:
                refused.append(guild_id)
                log.error("Discord refused to register commands in server %s", guild_id)
        if refused:
            message = (
                f"Discord refused to register commands in server(s) {', '.join(map(str, refused))}. "
                "Check each GUILD_ID is a server's ID (right-click the server icon, Copy Server ID) and that the bot "
                "has been invited there with the 'bot' and 'applications.commands' scopes."
            )
            if len(refused) == len(self.settings.guild_ids):
                raise SystemExit(message)
            log.warning(message)

    async def refit(self, game: str, credit_match: int | None = None) -> None:
        """Refits every rating for a game from its full match history. When
        credit_match is given, the movement is recorded as that match's Elo change."""
        async with self.fit_locks[game]:
            matches, anchors = await self.db.fit_inputs(game)
            before = await self.db.current_ratings(game)
            after = await asyncio.to_thread(refit, matches, anchors, GAMES[game].team_size)
            await self.db.write_ratings(game, after)
            if credit_match is not None:
                await self.db.add_match_deltas(credit_match, before, after)

    async def close(self) -> None:
        await self.henrik.close()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s, in servers: %s", self.user, ", ".join(f"{g.name} ({g.id})" for g in self.guilds) or "none")

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
