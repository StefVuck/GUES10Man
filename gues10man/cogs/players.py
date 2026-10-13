from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..games import DEFAULT_RATING, GAMES, Game
from ..rating import PERF_WEIGHT
from ..ranks import REGIONS, RankLookupError
from ..util import GAME_CHOICES, game_available, mention, fmt_delta, fmt_rating, is_staff, reply

if TYPE_CHECKING:
    from ..bot import TenManBot

PAGE_SIZE = 15


async def rank_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    game = GAMES.get(getattr(interaction.namespace, "game", None) or "valorant", GAMES["valorant"])
    return [app_commands.Choice(name=r, value=r) for r in game.ranks if current.lower() in r.lower()][:25]


def history_entry(game: Game, h) -> str:
    won = h["team"] == h["winner"]
    head = [f"**#{h['id']}**", "**Win**" if won else "Loss"]
    if h["score_a"] is not None:
        own, other = (h["score_a"], h["score_b"]) if h["team"] == "A" else (h["score_b"], h["score_a"])
        head.append(f"{own}-{other}")
    if h["map"]:
        head.append(h["map"])

    detail = [game.side(h["team"]) + (" (C)" if h["is_captain"] else "")]
    if h["rating_before"] is not None and h["rating_delta"] is not None:
        before, delta = round(h["rating_before"]), round(h["rating_delta"])
        detail.append(f"{before} → {before + delta} ({fmt_delta(delta)})")
    if h["score"] is not None:
        detail.append(f"{h['score']:g} {game.score_label}")
    detail.append(discord.utils.format_dt(datetime.fromtimestamp(h["finished_at"], tz=timezone.utc), "R"))
    return " · ".join(head) + "\n" + " · ".join(detail)


def history_embed(name: str, game: Game, rows) -> discord.Embed:
    wins = sum(h["team"] == h["winner"] for h in rows)
    net = sum(h["rating_delta"] or 0 for h in rows)
    embed = discord.Embed(
        title=f"{name}'s recent {game.name} matches",
        description=f"Last {len(rows)}: **{wins}W {len(rows) - wins}L** · Elo {fmt_delta(net)}\n\n"
        + "\n\n".join(history_entry(game, h) for h in rows),
        colour=discord.Colour.green() if net >= 0 else discord.Colour.red(),
    )
    return embed


COULOM_2007 = "https://doi.org/10.3233/ICG-2007-30403"
HUNTER_2004 = "https://doi.org/10.1214/aos/1079120141"


def elo_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="How the Elo works",
        description=(
            "It's not normal Elo. After every game the bot looks at every 10 man ever played and works out "
            "the ratings that best explain who beat who.\n\n"
            "- Your team's strength is everyone's Elo added up, so beating a stacked lobby gets you more.\n"
            "- You start at your rank (`/link` or `/setrank`), or 1000 if you skip that. It takes a fair few "
            "games to move far from it.\n"
            "- ACS only counts against your own teammates. It decides who gets more of the credit, never "
            "whether you won.\n"
            "- Your Elo can move on days you didn't play, because people you've played with or against got "
            "new results.\n"
            "- A 100 Elo gap between team averages is about a 64% chance to win."
        ),
        colour=discord.Colour(0xFF4655),
    )
    embed.add_field(
        name="Nerd stuff",
        value=(
            f"Team Bradley-Terry model fitted with the MM algorithm, from [Coulom 2007]({COULOM_2007}) "
            f"and [Hunter 2004]({HUNTER_2004}). Scoreboards are Plackett-Luce rankings weighted at "
            f"{PERF_WEIGHT:g} of a result."
        ),
        inline=False,
    )
    return embed


class LeaderboardView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed]):
        super().__init__(timeout=180)
        self.pages = pages
        self.index = 0
        self._sync()

    def _sync(self) -> None:
        self.prev.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index += 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


class Players(commands.Cog):
    elo = app_commands.Group(name="elo", description="About the rating system")
    def __init__(self, bot: TenManBot):
        self.bot = bot
        self.db = bot.db

    @app_commands.command(name="link", description="Link your Riot ID and seed your Valorant Elo from your rank")
    @app_commands.describe(riot_id="Your Riot ID, for example User#1234", region="Your account region")
    @app_commands.choices(region=[app_commands.Choice(name=r.upper(), value=r) for r in REGIONS])
    async def link(self, interaction: discord.Interaction, riot_id: str, region: str | None = None) -> None:
        region = region or self.bot.settings.valorant_region
        await interaction.response.defer(ephemeral=True)
        try:
            rank = await self.bot.henrik.fetch_rank(riot_id, region)
        except RankLookupError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await self.db.set_riot_id(interaction.user.id, rank.riot_id, region)
        existing = await self.db.get_rating(interaction.user.id, "valorant")
        game = GAMES["valorant"]
        summary = f"Linked **{rank.riot_id}**. Current rank: **{rank.current or 'Unranked'}**, peak: **{rank.peak or 'Unranked'}**."
        if existing and existing.games > 0:
            await interaction.followup.send(
                summary + f"\nYou've already played {existing.games} games so your Elo ({fmt_rating(existing.rating)}) stays as it is.",
                ephemeral=True,
            )
            return
        seed = game.rating_for_rank(rank.seed_rank) if rank.seed_rank else DEFAULT_RATING
        await self.db.seed_rating(interaction.user.id, "valorant", seed, rank.seed_rank)
        await self.bot.refit("valorant")
        await interaction.followup.send(
            summary + f"\nYour starting Elo is **{fmt_rating(seed)}** (seeded from {rank.seed_rank or 'the default'}).",
            ephemeral=True,
        )

    @app_commands.command(name="setrank", description="Set your rank manually to seed your Elo before your first game")
    @app_commands.choices(game=[c for c in GAME_CHOICES if GAMES[c.value].ranks])
    @game_available()
    @app_commands.autocomplete(rank=rank_autocomplete)
    async def setrank(
        self, interaction: discord.Interaction, game: str, rank: str, member: discord.Member | None = None
    ) -> None:
        target = member or interaction.user
        staff = await is_staff(interaction)
        if target.id != interaction.user.id and not staff:
            await reply(interaction, "Only staff can set someone else's rank.")
            return
        g = GAMES[game]
        rating = g.rating_for_rank(rank)
        if rating is None:
            await reply(interaction, f"`{rank}` isn't a {g.name} rank. Pick one from the suggestions.")
            return
        existing = await self.db.get_rating(target.id, game)
        if existing and existing.games > 0 and not staff:
            await reply(interaction, "You've already played rated games, so ask staff if your Elo needs adjusting.")
            return
        await interaction.response.defer(ephemeral=True)
        await self.db.seed_rating(target.id, game, rating, g.ranks[g.rank_index(rank)])
        await self.bot.refit(game)
        await reply(interaction, f"{target.mention}'s {g.name} Elo is now seeded from **{rank}** ({fmt_rating(rating)}).")

    @app_commands.command(name="profile", description="Show someone's 10 man record and Elo")
    @app_commands.choices(game=GAME_CHOICES)
    @game_available()
    async def profile(
        self, interaction: discord.Interaction, member: discord.Member | None = None, game: str = "valorant"
    ) -> None:
        target = member or interaction.user
        g = GAMES[game]
        rating = await self.db.get_rating(target.id, game)
        if not rating:
            await reply(interaction, f"{target.mention} hasn't played any {g.name} 10 mans yet.")
            return
        player = await self.db.get_player(target.id)
        history = await self.db.history(target.id, game, limit=10)
        maps = await self.db.map_stats(target.id, game)
        mates = await self.db.teammate_stats(target.id, game)
        cap_played, cap_won = await self.db.captain_record(target.id, game)

        embed = discord.Embed(title=f"{target.display_name} · {g.name}", colour=discord.Colour(0xFF4655))
        embed.set_thumbnail(url=target.display_avatar.url)
        winrate = f"{rating.wins / rating.games:.0%}" if rating.games else "-"
        approx = g.rank_for_rating(rating.rating)
        elo_line = f"**{fmt_rating(rating.rating)}**" + (f" (≈ {approx})" if approx else "")
        if rating.games < 10:
            elo_line += " · provisional"
        embed.add_field(name="Elo", value=elo_line)
        embed.add_field(name="Peak Elo", value=fmt_rating(rating.peak_rating))
        embed.add_field(name="Record", value=f"{rating.wins}W {rating.losses}L ({winrate})")
        if player and player["riot_id"] and game == "valorant":
            embed.add_field(name="Riot ID", value=player["riot_id"])
        if rating.seed_rank:
            embed.add_field(name="Seeded from", value=rating.seed_rank)
        if cap_played:
            embed.add_field(name="As captain", value=f"{cap_won}W {cap_played - cap_won}L")

        if history:
            results = ["W" if h["team"] == h["winner"] else "L" for h in history]
            streak = len(results) - len("".join(results).lstrip(results[0]))
            form = " ".join(results[::-1])
            embed.add_field(name="Form (oldest → newest)", value=f"`{form}` · current streak {streak}{results[0]}", inline=False)
            trend = sum(h["rating_delta"] or 0 for h in history)
            embed.add_field(name=f"Elo over last {len(history)}", value=fmt_delta(trend))
        if maps:
            best = sorted(maps, key=lambda r: (r["won"] / r["played"], r["played"]), reverse=True)[:5]
            embed.add_field(
                name="Maps",
                value="\n".join(f"{r['map']}: {r['won']}W {r['played'] - r['won']}L" for r in best),
            )
        if mates:
            embed.add_field(
                name="Best duo partners",
                value="\n".join(f"{mention(r['user_id'])} {r['won']}/{r['played']}" for r in mates[:3]),
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="history", description="Recent 10 man matches for a player")
    @app_commands.choices(game=GAME_CHOICES)
    @game_available()
    async def history(
        self, interaction: discord.Interaction, member: discord.Member | None = None, game: str = "valorant"
    ) -> None:
        target = member or interaction.user
        g = GAMES[game]
        rows = await self.db.history(target.id, game, limit=10)
        if not rows:
            await reply(interaction, f"No completed {g.name} matches for {target.mention} yet.")
            return
        await interaction.response.send_message(embed=history_embed(target.display_name, g, rows))

    @elo.command(name="help", description="Explain how Elo is calculated, with the research it's based on")
    @app_commands.describe(public="Post it in the channel instead of only showing it to you")
    async def elo_help(self, interaction: discord.Interaction, public: bool = False) -> None:
        await interaction.response.send_message(embed=elo_help_embed(), ephemeral=not public)

    @app_commands.command(name="leaderboard", description="10 man leaderboard")
    @app_commands.choices(
        game=GAME_CHOICES,
        sort=[
            app_commands.Choice(name="Elo", value="elo"),
            app_commands.Choice(name="Wins", value="wins"),
            app_commands.Choice(name="Win rate", value="winrate"),
        ],
    )
    @app_commands.describe(min_games="Hide players with fewer games than this")
    @game_available()
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        game: str = "valorant",
        sort: str = "elo",
        min_games: app_commands.Range[int, 0, 100] = 1,
    ) -> None:
        g = GAMES[game]
        rows = await self.db.leaderboard(game, sort, min_games)
        if not rows:
            await reply(interaction, f"Nobody has played enough {g.name} 10 mans yet.", ephemeral=False)
            return
        pages = []
        for start in range(0, len(rows), PAGE_SIZE):
            lines = []
            for i, r in enumerate(rows[start : start + PAGE_SIZE], start + 1):
                wr = f"{r.wins / r.games:.0%}" if r.games else "-"
                lines.append(f"`{i:>2}` {mention(r.user_id)} **{fmt_rating(r.rating)}** · {r.wins}W {r.losses}L ({wr})")
            embed = discord.Embed(title=f"{g.name} leaderboard", description="\n".join(lines), colour=discord.Colour.gold())
            embed.set_footer(text=f"Page {len(pages) + 1} of {-(-len(rows) // PAGE_SIZE)} · min {min_games} games")
            pages.append(embed)
        view = LeaderboardView(pages) if len(pages) > 1 else discord.utils.MISSING
        await interaction.response.send_message(embed=pages[0], view=view)


async def setup(bot: TenManBot) -> None:
    await bot.add_cog(Players(bot))
