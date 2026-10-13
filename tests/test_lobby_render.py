from types import SimpleNamespace

import pytest

from gues10man.cogs.lobby import DraftState, Lobby
from gues10man.db import Database


@pytest.fixture
async def lobby(tmp_path):
    db = Database(str(tmp_path / "lobby.db"))
    await db.connect()
    bot = SimpleNamespace(db=db, get_guild=lambda _id: None)
    yield Lobby(bot)
    await db.close()


async def test_render_each_stage(lobby):
    match_id = await lobby.db.create_match(1, "valorant", list(range(10)))
    _, view = await lobby.render_match(match_id)
    assert {c.custom_id for c in view.children} >= {f"m:auto:{match_id}", f"m:draft:{match_id}"}

    state = DraftState(captains=(0, 1), order=[0, 1, 1, 0, 0, 1, 1, 0], teams=([0], [1]), pool=list(range(2, 10)))
    lobby.drafts[match_id] = state
    await lobby.db.set_teams(match_id, [0], [1], (0, 1))
    await lobby.db.update_match(match_id, status="drafting")
    embed, view = await lobby.render_match(match_id)
    assert "<@0> is picking" in embed.description
    assert len(view.children[0].item.options) == 8

    for uid in range(2, 10):
        state.pick(uid)
    assert state.done and len(state.teams[0]) == len(state.teams[1]) == 5

    await lobby.db.set_teams(match_id, state.teams[0], state.teams[1], state.captains, state.pick_log)
    await lobby.db.update_match(match_id, status="live", map="Bind")
    embed, view = await lobby.render_match(match_id)
    assert "Predicted" in embed.description
    assert f"m:wina:{match_id}" in {c.custom_id for c in view.children}


class FakeChannel:
    def __init__(self):
        self.sent = []

    def get_partial_message(self, message_id):
        return None

    async def send(self, content, **kwargs):
        self.sent.append(content)


async def test_concurrent_matches_announce_their_own_teams(tmp_path, monkeypatch):
    import discord

    db = Database(str(tmp_path / "two.db"))
    await db.connect()
    channel = FakeChannel()
    monkeypatch.setattr(discord.abc, "Messageable", FakeChannel)
    cog = Lobby(SimpleNamespace(db=db, get_guild=lambda _id: None, get_channel=lambda _id: channel))

    for uid in range(20):
        await db.join_queue(1, "valorant", uid)
    first = await db.create_match(1, "valorant", (await db.queue_members(1, "valorant"))[:10])
    second = await db.create_match(1, "valorant", (await db.queue_members(1, "valorant"))[:10])
    assert first != second and await db.queue_members(1, "valorant") == []
    assert await db.active_match_for(3) == first and await db.active_match_for(13) == second

    for match_id, base in ((first, 0), (second, 10)):
        ids = list(range(base, base + 10))
        await db.set_teams(match_id, ids[:5], ids[5:], (ids[0], ids[5]))
        await db.update_match(match_id, status="live", map="Lotus", channel_id=99)
        await cog.announce_teams(match_id)

    one, two = channel.sent
    assert f"Match #{first}" in one and "<@4>" in one and "<@14>" not in one
    assert f"Match #{second}" in two and "👑 <@15>" in two.split("Team B:")[1]
    await db.close()
