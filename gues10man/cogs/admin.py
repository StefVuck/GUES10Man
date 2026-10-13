from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..games import DEFAULT_RATING, GAMES
from ..util import GAME_CHOICES, game_available, fmt_rating, reply, staff_only

if TYPE_CHECKING:
    from ..bot import TenManBot


class Admin(commands.Cog):
    setup_group = app_commands.Group(
        name="setup",
        description="Configure the 10 man bot",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    rating = app_commands.Group(name="rating", description="Adjust player Elo", guild_only=True)

    def __init__(self, bot: TenManBot):
        self.bot = bot
        self.db = bot.db

    @setup_group.command(name="staffrole", description="Role allowed to run queues, cancel matches and fix results")
    async def setup_staffrole(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        assert interaction.guild is not None
        await self.db.update_config(interaction.guild.id, staff_role_id=role.id if role else None)
        await reply(interaction, f"Staff role set to {role.mention}." if role else "Staff role cleared; only Manage Server can run staff commands.")

    @rating.command(name="seed", description="Set the Elo a player's rating is anchored to; results still move them from there")
    @app_commands.choices(game=GAME_CHOICES)
    @game_available()
    @staff_only()
    async def rating_seed(
        self, interaction: discord.Interaction, member: discord.Member, game: str, elo: app_commands.Range[int, 0, 4000]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        existing = await self.db.get_rating(member.id, game)
        await self.db.seed_rating(member.id, game, float(elo), existing.seed_rank if existing else None)
        await self.bot.refit(game)
        now = await self.db.get_rating(member.id, game)
        await reply(interaction, f"{member.mention}'s {GAMES[game].name} seed is **{elo}**; their Elo is now **{fmt_rating(now.rating)}**.")

    @rating.command(name="adjust", description="Shift a player's seed up or down")
    @app_commands.choices(game=GAME_CHOICES)
    @game_available()
    @staff_only()
    async def rating_adjust(
        self, interaction: discord.Interaction, member: discord.Member, game: str, amount: app_commands.Range[int, -1000, 1000]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        current = await self.db.get_or_create_rating(member.id, game, DEFAULT_RATING)
        await self.db.seed_rating(member.id, game, current.anchor + amount, current.seed_rank)
        await self.bot.refit(game)
        now = await self.db.get_rating(member.id, game)
        await reply(
            interaction,
            f"{member.mention}'s {GAMES[game].name} seed {fmt_rating(current.anchor)} → {fmt_rating(current.anchor + amount)}; "
            f"Elo {fmt_rating(current.rating)} → **{fmt_rating(now.rating)}**.",
        )


async def setup(bot: TenManBot) -> None:
    await bot.add_cog(Admin(bot))
