from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from .games import GAMES

if TYPE_CHECKING:
    from .bot import TenManBot

# Sandbox players created by /test run. They have no Discord account, so they
# are shown by name instead of as a mention.
FAKE_PLAYERS: dict[int, str] = {}


def mention(user_id: int) -> str:
    name = FAKE_PLAYERS.get(user_id)
    return f"**{name}**" if name else f"<@{user_id}>"


GAME_CHOICES = [
    app_commands.Choice(name=g.name if g.available else f"{g.name} (coming soon)", value=g.key) for g in GAMES.values()
]


def game_available():
    """Commands still list every game, but only Valorant (and the sandbox) actually run for now."""

    async def predicate(interaction: discord.Interaction) -> bool:
        key = getattr(interaction.namespace, "game", None)
        game = GAMES.get(key) if key else None
        if game and not game.available:
            raise app_commands.CheckFailure(f"{game.name} support is coming soon. Only Valorant works for now.")
        return True

    return app_commands.check(predicate)


async def is_staff(interaction: discord.Interaction) -> bool:
    bot: TenManBot = interaction.client  # type: ignore[assignment]
    member = interaction.user
    if member.id in bot.settings.staff_user_ids or await bot.is_owner(member):
        return True
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.manage_guild:
        return True
    config = await bot.db.get_config(member.guild.id)
    return bool(config.staff_role_id and member.get_role(config.staff_role_id))


CHANNEL_PERMISSIONS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}


def missing_channel_permissions(interaction: discord.Interaction) -> str | None:
    """Slash commands reach the bot even in channels it can't post in, so check
    before starting anything that posts messages."""
    perms = interaction.app_permissions
    missing = [label for attr, label in CHANNEL_PERMISSIONS.items() if not getattr(perms, attr)]
    if not missing:
        return None
    return (
        f"I can't post in this channel because I'm missing: {', '.join(missing)}. "
        "Give the bot (or its role) these permissions here, or use a channel it can already see."
    )


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
