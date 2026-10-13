from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..games import DEFAULT_RATING, GAMES
from ..util import GAME_CHOICES, fmt_rating, reply, staff_only

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

    @setup_group.command(name="voice", description="Set the lobby and team voice channels players are moved between")
    async def setup_voice(
        self,
        interaction: discord.Interaction,
        lobby: discord.VoiceChannel,
        team_a: discord.VoiceChannel,
        team_b: discord.VoiceChannel,
    ) -> None:
        assert interaction.guild is not None
        await self.db.update_config(
            interaction.guild.id, lobby_vc_id=lobby.id, team_a_vc_id=team_a.id, team_b_vc_id=team_b.id
        )
        me = interaction.guild.me
        missing = [c.mention for c in (lobby, team_a, team_b) if not c.permissions_for(me).move_members]
        note = f"\n⚠️ I don't have **Move Members** in {', '.join(missing)}." if missing else ""
        await reply(interaction, f"Players will be moved {lobby.mention} → {team_a.mention} / {team_b.mention} and back.{note}")

    @setup_group.command(name="staffrole", description="Role allowed to run queues, cancel matches and fix results")
    async def setup_staffrole(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        assert interaction.guild is not None
        await self.db.update_config(interaction.guild.id, staff_role_id=role.id if role else None)
        await reply(interaction, f"Staff role set to {role.mention}." if role else "Staff role cleared; only Manage Server can run staff commands.")

    @rating.command(name="set", description="Set a player's Elo directly")
    @app_commands.choices(game=GAME_CHOICES)
    @staff_only()
    async def rating_set(
        self, interaction: discord.Interaction, member: discord.Member, game: str, elo: app_commands.Range[int, 0, 4000]
    ) -> None:
        existing = await self.db.get_rating(member.id, game)
        await self.db.seed_rating(member.id, game, float(elo), existing.seed_rank if existing else None)
        await reply(interaction, f"{member.mention}'s {GAMES[game].name} Elo set to **{elo}**.")

    @rating.command(name="adjust", description="Add or remove Elo from a player")
    @app_commands.choices(game=GAME_CHOICES)
    @staff_only()
    async def rating_adjust(
        self, interaction: discord.Interaction, member: discord.Member, game: str, amount: app_commands.Range[int, -1000, 1000]
    ) -> None:
        current = await self.db.get_or_create_rating(member.id, game, DEFAULT_RATING)
        new = current.rating + amount
        await self.db.seed_rating(member.id, game, new, current.seed_rank)
        await reply(interaction, f"{member.mention}'s {GAMES[game].name} Elo: {fmt_rating(current.rating)} → **{fmt_rating(new)}**.")


async def setup(bot: TenManBot) -> None:
    await bot.add_cog(Admin(bot))
