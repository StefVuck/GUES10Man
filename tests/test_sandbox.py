import random
from itertools import count
from types import SimpleNamespace

import pytest

from gues10man.bot import EXTENSIONS, TenManBot
from gues10man.config import Settings
from gues10man.util import FAKE_PLAYERS, mention


class RecordingChannel:
    def __init__(self):
        self.sent = []
        self.ids = count(1000)

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
        return SimpleNamespace(id=next(self.ids), channel=SimpleNamespace(id=99), guild=None)


@pytest.fixture
async def bot(tmp_path):
    bot = TenManBot(Settings("token", None, None, "eu", str(tmp_path / "sandbox.db")))
    await bot.db.connect()
    yield bot
    await bot.db.close()
    for uid in range(1, 11):
        FAKE_PLAYERS.pop(uid, None)


async def test_sandbox_session(bot):
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    sandbox = bot.get_cog("Sandbox")
    channel = RecordingChannel()
    tester = 555_000_000_000_000_001
    rng = random.Random(3)

    roster = await sandbox.setup_players(tester)
    assert roster[0] == tester and len(roster) == 10
    assert mention(1) == "**Bot Ace**" and mention(tester) == f"<@{tester}>"

    first = await sandbox.draft_game(channel, 1, roster, 0, rng)
    assert (await bot.db.get_match(first))["status"] == "live"
    assert (await bot.db.get_match(first))["mode"] == "draft-top"
    await sandbox.play(first, tester, scoreboard=False, rng=rng)

    for _ in range(3):
        match_id = await sandbox.auto_game(channel, 1, roster, 0)
        summary = await sandbox.play(match_id, tester, scoreboard=True, rng=rng)
        assert summary.startswith(f"#{match_id}:")
        assert all(p["score"] is not None for p in await bot.db.match_players(match_id))

    me = await bot.db.get_rating(tester, "sandbox")
    assert me.games == 4
    assert len(await bot.db.history(tester, "sandbox")) == 4
    board = await bot.db.leaderboard("sandbox", "elo", min_games=1)
    assert len(board) == 10
    assert await bot.db.get_rating(tester, "valorant") is None

    await bot.db.wipe_game("sandbox")
    assert await bot.db.leaderboard("sandbox", "elo", min_games=0) == []
