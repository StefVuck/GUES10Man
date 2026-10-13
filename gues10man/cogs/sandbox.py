from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..elo import expected
from ..games import GAMES
from ..util import FAKE_PLAYERS, missing_channel_permissions, reply, staff_only

if TYPE_CHECKING:
    from ..bot import TenManBot
    from .lobby import Lobby

log = logging.getLogger(__name__)

GAME = "sandbox"
FAKES = {
    1: ("Bot Ace", "Silver 2"),
    2: ("Bot Blitz", "Gold 1"),
    3: ("Bot Cipher", "Gold 3"),
    4: ("Bot Drift", "Platinum 1"),
    5: ("Bot Echo", "Platinum 2"),
    6: ("Bot Flare", "Platinum 3"),
    7: ("Bot Ghost", "Diamond 1"),
    8: ("Bot Hex", "Diamond 3"),
    9: ("Bot Ion", "Ascendant 1"),
    10: ("Bot Jett", "Ascendant 3"),
}
TESTER_DEFAULT_RANK = "Gold 3"


def hidden_skill(user_id: int, anchor: float) -> float:
    """Each fake has a fixed true skill near their seed, so repeated runs tell a consistent story."""
    return anchor + random.Random(user_id).gauss(0, 150)


class Sandbox(commands.Cog):
    test = app_commands.Group(name="test", description="Sandbox for trying the bot with fake players", guild_only=True)

    def __init__(self, bot: TenManBot):
        self.bot = bot
        self.db = bot.db
        self.running = False

    @property
    def lobby(self) -> Lobby:
        return self.bot.get_cog("Lobby")  # type: ignore[return-value]

    async def setup_players(self, tester: int | None) -> list[int]:
        game = GAMES[GAME]
        await self.db.add_fake_players({uid: name for uid, (name, _) in FAKES.items()})
        FAKE_PLAYERS.update({uid: name for uid, (name, _) in FAKES.items()})
        for uid, (_, rank) in FAKES.items():
            if not await self.db.get_rating(uid, GAME):
                await self.db.seed_rating(uid, GAME, game.rating_for_rank(rank), rank)
        roster = list(FAKES)
        if tester is not None:
            if not await self.db.get_rating(tester, GAME):
                real = await self.db.get_rating(tester, "valorant")
                if real:
                    await self.db.seed_rating(tester, GAME, real.anchor, real.seed_rank)
                else:
                    await self.db.seed_rating(tester, GAME, game.rating_for_rank(TESTER_DEFAULT_RANK), TESTER_DEFAULT_RANK)
            roster = [tester] + roster[:9]
        await self.bot.refit(GAME)
        return roster

    async def skills(self, user_ids: list[int]) -> dict[int, float]:
        out = {}
        for uid in user_ids:
            rating = await self.db.get_rating(uid, GAME)
            out[uid] = hidden_skill(uid, rating.anchor)
        return out

    async def play(self, match_id: int, reporter: int, scoreboard: bool, rng: random.Random) -> str:
        players = await self.db.match_players(match_id)
        skill = await self.skills([p["user_id"] for p in players])
        team = {p["user_id"]: p["team"] for p in players}
        avg = {k: sum(skill[u] for u, t in team.items() if t == k) / 5 for k in ("A", "B")}
        winner = "A" if rng.random() < expected(avg["A"], avg["B"]) else "B"
        loser_rounds = rng.randint(3, 11)
        score = (13, loser_rounds) if winner == "A" else (loser_rounds, 13)
        error = await self.lobby.record_result(match_id, winner, score, reporter)
        if error:
            raise RuntimeError(error)
        if scoreboard:
            lobby_avg = sum(skill.values()) / len(skill)
            acs = {
                u: float(int(min(400, max(80, rng.gauss(210 + (skill[u] - lobby_avg) / 3 + (20 if t == winner else -15), 40)))))
                for u, t in team.items()
            }
            error = await self.lobby.record_scoreboard(match_id, acs)
            if error:
                raise RuntimeError(error)
        side = GAMES[GAME].side(winner)
        return f"#{match_id}: {side} won {max(score)}-{min(score)}"

    async def draft_game(self, channel: discord.abc.Messageable, guild_id: int, roster: list[int], step: float, rng: random.Random) -> int:
        lobby = self.lobby
        match_id = await self.db.create_match(guild_id, GAME, roster)
        await lobby.post_match(channel, match_id)
        await asyncio.sleep(step)
        async with lobby.locks[("match", match_id)]:
            await lobby.start_draft(None, match_id, "top")
        while match_id in lobby.drafts:
            await asyncio.sleep(step)
            state = lobby.drafts[match_id]
            ratings = await lobby.ratings(state.pool, GAME)
            ranked = sorted(state.pool, key=lambda u: ratings[u].rating, reverse=True)
            choice = ranked[0] if rng.random() < 0.7 else rng.choice(ranked[:3])
            async with lobby.locks[("match", match_id)]:
                await lobby.pick(None, match_id, choice)
        return match_id

    async def auto_game(self, channel: discord.abc.Messageable, guild_id: int, roster: list[int], step: float) -> int:
        lobby = self.lobby
        match_id = await self.db.create_match(guild_id, GAME, roster)
        await lobby.post_match(channel, match_id)
        await asyncio.sleep(step)
        async with lobby.locks[("match", match_id)]:
            await lobby.start_auto(None, match_id)
        return match_id

    @test.command(name="run", description="Play a drafted game and three auto-balanced games with fake players")
    @app_commands.describe(
        include_me="Put yourself in the lobby so /profile and /history have something to show",
        step_seconds="Pause between steps so you can watch the messages update",
    )
    @staff_only()
    async def run(
        self,
        interaction: discord.Interaction,
        include_me: bool = True,
        step_seconds: app_commands.Range[float, 0, 5] = 1.5,
    ) -> None:
        if self.running:
            await reply(interaction, "A sandbox run is already in progress.")
            return
        if not isinstance(interaction.channel, discord.abc.Messageable) or interaction.guild is None:
            await reply(interaction, "Run this in a server text channel.")
            return
        problem = missing_channel_permissions(interaction)
        if problem:
            await reply(interaction, problem)
            return
        self.running = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.cancel_leftovers()
            tester = interaction.user.id if include_me else None
            note = ""
            if tester is not None and await self.db.active_match_for(tester):
                tester, note = None, "You're in a live match, so the sandbox used fake players only.\n"
            roster = await self.setup_players(tester)
            rng = random.Random()
            channel, guild_id, me = interaction.channel, interaction.guild.id, interaction.user.id

            results = []
            match_id = await self.draft_game(channel, guild_id, roster, step_seconds, rng)
            await asyncio.sleep(step_seconds)
            results.append(await self.play(match_id, me, scoreboard=False, rng=rng) + " (captains draft)")

            for _ in range(3):
                await asyncio.sleep(step_seconds)
                match_id = await self.auto_game(channel, guild_id, roster, step_seconds)
                await asyncio.sleep(step_seconds)
                results.append(await self.play(match_id, me, scoreboard=True, rng=rng) + " (auto balance, with ACS)")

            await reply(
                interaction,
                note
                + "Sandbox run finished.\n"
                + "\n".join(results)
                + "\n\nTry `/leaderboard game:Sandbox`, `/profile game:Sandbox` and `/history game:Sandbox`. "
                "Run `/test run` again to add more games, or `/test reset` to wipe the sandbox.",
            )
        except Exception as e:
            log.exception("Sandbox run failed")
            await self.cancel_leftovers()
            await reply(interaction, f"The sandbox run failed: {e}")
        finally:
            self.running = False

    async def cancel_leftovers(self) -> None:
        """Only one run happens at a time, so any unfinished sandbox match is from a run that failed."""
        for m in await self.db.active_matches():
            if m["game"] == GAME:
                self.lobby.drafts.pop(m["id"], None)
                await self.db.update_match(m["id"], status="cancelled")

    @test.command(name="reset", description="Delete all sandbox matches and ratings")
    @staff_only()
    async def reset(self, interaction: discord.Interaction) -> None:
        if self.running:
            await reply(interaction, "Wait for the current sandbox run to finish.")
            return
        await self.db.wipe_game(GAME)
        await reply(interaction, "Sandbox wiped. Real games are untouched.")


async def setup(bot: TenManBot) -> None:
    await bot.add_cog(Sandbox(bot))
