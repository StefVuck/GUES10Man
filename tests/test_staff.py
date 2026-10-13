from types import SimpleNamespace

from gues10man.config import Settings
from gues10man.util import is_staff


def interaction(user_id, owner_id=None, staff=()):
    async def is_owner(user):
        return user.id == owner_id

    settings = Settings("t", (), None, "eu", ":memory:", frozenset(staff))
    client = SimpleNamespace(settings=settings, is_owner=is_owner)
    return SimpleNamespace(user=SimpleNamespace(id=user_id), client=client)


async def test_bot_owner_is_staff():
    assert await is_staff(interaction(1, owner_id=1))


async def test_listed_user_is_staff():
    assert await is_staff(interaction(2, staff={2}))


async def test_other_users_are_not():
    assert not await is_staff(interaction(3, owner_id=1, staff={2}))


def test_missing_channel_permissions():
    import discord

    from gues10man.util import missing_channel_permissions

    ok = SimpleNamespace(app_permissions=discord.Permissions(309237730304))
    assert missing_channel_permissions(ok) is None
    hidden = SimpleNamespace(app_permissions=discord.Permissions(send_messages=True))
    assert "View Channel" in missing_channel_permissions(hidden)


def test_guild_ids_parse_from_env(monkeypatch):
    monkeypatch.setattr("gues10man.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("GUILD_ID", "111, 222")
    assert Settings.from_env().guild_ids == (111, 222)
    monkeypatch.setenv("GUILD_ID", "")
    assert Settings.from_env().guild_ids == ()


async def test_other_games_are_coming_soon():
    import pytest
    from discord import app_commands

    from gues10man.games import GAMES
    from gues10man.util import GAME_CHOICES, game_available

    check = game_available()(lambda: None).__discord_app_commands_checks__[0]
    for key in ("valorant", "sandbox"):
        assert await check(SimpleNamespace(namespace=SimpleNamespace(game=key)))
    with pytest.raises(app_commands.CheckFailure, match="coming soon"):
        await check(SimpleNamespace(namespace=SimpleNamespace(game="cs2")))
    assert "Summit" in GAMES["valorant"].maps
    assert {c.name for c in GAME_CHOICES} >= {"Valorant", "Counter-Strike 2 (coming soon)"}
