from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ..games import GAMES
from ..importer import ImportedResult, match_game
from ..ranks import RankLookupError
from ..util import mention, reply

if TYPE_CHECKING:
    from ..bot import TenManBot
    from .lobby import Lobby

log = logging.getLogger(__name__)

SEARCH_ATTEMPTS = 10
SEARCH_INTERVAL = 60
QUERIES_PER_ATTEMPT = 2
MATCHES_PER_QUERY = 5


class Importer(commands.Cog):
    """Fetches a finished match's result from Riot's match history once a captain says it's done."""

    def __init__(self, bot: TenManBot):
        self.bot = bot
        self.db = bot.db
        self.searches: dict[int, asyncio.Task] = {}

    @property
    def lobby(self) -> Lobby:
        return self.bot.get_cog("Lobby")  # type: ignore[return-value]

    async def linked_players(self, match_id: int):
        """Our linked players, split by team, with captains first in the query order."""
        team_a, team_b, users, queries = set(), set(), {}, []
        unlinked = []
        players = sorted(await self.db.match_players(match_id), key=lambda p: not p["is_captain"])
        for p in players:
            player = await self.db.get_player(p["user_id"])
            if not player or not player["riot_id"]:
                unlinked.append(p["user_id"])
                continue
            riot = player["riot_id"].lower()
            (team_a if p["team"] == "A" else team_b).add(riot)
            users[riot] = p["user_id"]
            queries.append((player["riot_id"], player["region"] or self.bot.settings.valorant_region))
        return team_a, team_b, users, queries, unlinked

    async def start(self, interaction: discord.Interaction, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        if not m or m["status"] != "live":
            await reply(interaction, f"Match #{match_id} isn't live, so there's nothing to fetch.")
            return
        if m["game"] != "valorant" or not self.bot.henrik.enabled:
            await reply(interaction, "Results can only be fetched for Valorant matches with HENRIK_API_KEY set. Report it with the buttons instead.")
            return
        running = self.searches.get(match_id)
        if running and not running.done():
            await reply(interaction, f"Already looking for match #{match_id} in Riot's history.")
            return
        team_a, team_b, _, queries, _ = await self.linked_players(match_id)
        if len(queries) < 2:
            await reply(
                interaction,
                "Fewer than two players in this match have linked their Riot ID with `/link`, so I can't find the game. "
                "Report the winner with the buttons instead.",
            )
            return
        if not isinstance(interaction.channel, discord.abc.Messageable):
            await reply(interaction, "Use this in a server text channel.")
            return
        await reply(interaction, f"Looking for match #{match_id} in Riot's match history.")
        status = await interaction.channel.send(
            f"Fetching the result of match #{match_id} from Riot. Games can take a few minutes to appear, "
            f"so I'll keep checking for up to {SEARCH_ATTEMPTS} minutes."
        )
        task = asyncio.create_task(self.search(match_id, status, interaction.user.id))
        self.searches[match_id] = task
        task.add_done_callback(lambda _: self.searches.pop(match_id, None))

    async def find(self, match_id: int) -> tuple[ImportedResult | None, list[str]]:
        m = await self.db.get_match(match_id)
        team_a, team_b, _, queries, _ = await self.linked_players(match_id)
        live_since = datetime.fromtimestamp(m["live_at"] or m["created_at"], tz=timezone.utc)
        problems = []
        for riot_id, region in queries[:QUERIES_PER_ATTEMPT]:
            try:
                games, _, _ = await self.bot.henrik.fetch_custom_matches(riot_id, region, MATCHES_PER_QUERY)
            except RankLookupError as e:
                problems.append(str(e))
                continue
            result = match_game(games, team_a, team_b, live_since)
            if result:
                return result, problems
        return None, problems

    async def search(self, match_id: int, status: discord.Message, reporter: int) -> None:
        async def say(text: str) -> None:
            try:
                await status.edit(content=text)
            except discord.HTTPException:
                pass

        try:
            for attempt in range(1, SEARCH_ATTEMPTS + 1):
                m = await self.db.get_match(match_id)
                if not m or m["status"] != "live":
                    await say(f"Stopped looking for match #{match_id}: it has already been reported or cancelled.")
                    return
                result, problems = await self.find(match_id)
                if result:
                    await say(await self.record(match_id, result, reporter))
                    return
                note = f" (API: {problems[-1]})" if problems else ""
                await say(
                    f"Match #{match_id} isn't in Riot's history yet, attempt {attempt} of {SEARCH_ATTEMPTS}{note}. "
                    "Checking again in a minute. Captains can still report it with the buttons."
                )
                await asyncio.sleep(SEARCH_INTERVAL)
            await say(
                f"Couldn't find match #{match_id} in Riot's history after {SEARCH_ATTEMPTS} minutes. "
                "Report the winner with the buttons, or press Game finished to try again."
            )
        except Exception:
            log.exception("Result search for match %s failed", match_id)
            await say(f"Something went wrong fetching match #{match_id}. Report it with the buttons instead.")

    async def record(self, match_id: int, result: ImportedResult, reporter: int) -> str:
        _, _, users, _, unlinked = await self.linked_players(match_id)
        scores = {users[r]: acs for r, acs in result.acs.items() if r in users}
        error = await self.lobby.record_result(
            match_id, result.winner, result.score, reporter, scores=scores, map_name=result.game.map
        )
        if error:
            return f"Found the game in Riot's history, but couldn't record it: {error}"
        m = await self.db.get_match(match_id)
        game = GAMES[m["game"]]
        a, b = result.score
        text = (
            f"Found match #{match_id} in Riot's history: {result.game.map}, "
            f"**{game.side(result.winner)} won {max(a, b)}-{min(a, b)}**. "
            f"Result recorded with {game.score_label} for {len(scores)} of 10 players."
        )
        if unlinked:
            text += f" No {game.score_label} for " + " ".join(mention(u) for u in unlinked) + " (Riot ID not linked)."
        return text


async def setup(bot: TenManBot) -> None:
    await bot.add_cog(Importer(bot))
