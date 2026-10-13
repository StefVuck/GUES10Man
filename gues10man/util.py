from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from .games import GAMES

if TYPE_CHECKING:
    from .bot import TenManBot

GAME_CHOICES = [app_commands.Choice(name=g.name, value=g.key) for g in GAMES.values()]


async def is_staff(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.manage_guild:
        return True
    bot: TenManBot = interaction.client  # type: ignore[assignment]
    config = await bot.db.get_config(member.guild.id)
    return bool(config.staff_role_id and member.get_role(config.staff_role_id))


def staff_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if await is_staff(interaction):
            return True
        raise app_commands.CheckFailure("Only staff can use this command.")

    return app_commands.check(predicate)


def fmt_rating(r: float) -> str:
    return str(round(r))


def fmt_delta(d: float) -> str:
    n = round(d)
    return f"+{n}" if n >= 0 else str(n)


async def reply(interaction: discord.Interaction, content: str, ephemeral: bool = True) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(content, ephemeral=ephemeral)
