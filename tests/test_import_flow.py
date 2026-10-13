import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gues10man.bot import EXTENSIONS, TenManBot
from gues10man.config import Settings
from gues10man.customs import CustomGame, CustomPlayer


@pytest.fixture
async def bot(tmp_path):
    bot = TenManBot(Settings("token", None, None, "eu", str(tmp_path / "import.db")))
    await bot.db.connect()
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    yield bot
    await bot.db.close()


async def test_finished_game_is_imported(bot):
    db = bot.db
    users = list(range(100, 110))
    match_id = await db.create_match(1, "valorant", users)
    for uid in users[:8]:
        await db.set_riot_id(uid, f"P{uid}#EUW", "eu")
        await db.get_or_create_rating(uid, "valorant", 1000)
    for uid in users[8:]:
        await db.get_or_create_rating(uid, "valorant", 1000)
    await db.set_teams(match_id, users[:5], users[5:], (users[0], users[5]))
    await db.update_match(match_id, status="live", map="Bind", live_at=int(time.time()))

    started = datetime.now(timezone.utc) + timedelta(minutes=1)
    players = [CustomPlayer(f"P{u}", "EUW", "Blue", "Jett", 5000 - (u % 5) * 600, 1, 1, 1) for u in users[:5]]
    players += [CustomPlayer(f"P{u}", "EUW", "Red", "Sova", 3000, 1, 1, 1) for u in users[5:8]]
    players += [CustomPlayer("Stranger", f"{i}000", "Red", "Omen", 3000, 1, 1, 1) for i in range(2)]
    game = CustomGame("m1", "Ascent", "Custom Game", started, 22, {"Blue": 13, "Red": 9}, "Blue", players)

    async def fetch(riot_id, region, size):
        return [game], {}, "v4"

    bot.henrik = SimpleNamespace(enabled=True, fetch_custom_matches=fetch)
    importer = bot.get_cog("Importer")
    result, problems = await importer.find(match_id)
    assert result and not problems
    message = await importer.record(match_id, result, reporter=users[0])

    m = await db.get_match(match_id)
    assert (m["status"], m["winner"], m["score_a"], m["score_b"], m["map"]) == ("complete", "A", 13, 9, "Ascent")
    scored = {p["user_id"]: p["score"] for p in await db.match_players(match_id)}
    assert scored[100] == pytest.approx(5000 / 22) and scored[108] is None
    assert "8 of 10 players" in message and "<@108>" in message
