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
