import pytest

from gues10man.db import Database
from gues10man.elo import compute_deltas


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


async def test_queue_pop_and_result_roundtrip(db):
    for uid in range(12):
        await db.get_or_create_rating(uid, "valorant", 1000)
        await db.join_queue(1, "valorant", uid)
    members = await db.queue_members(1, "valorant")
    match_id = await db.create_match(1, "valorant", members[:10])
    assert await db.queue_members(1, "valorant") == [10, 11]
    assert await db.active_match_for(3) == match_id

    await db.set_teams(match_id, list(range(5)), list(range(5, 10)), (0, 5))
    await db.update_match(match_id, status="live", map="Ascent")
    ratings = {uid: (await db.get_rating(uid, "valorant")).as_player() for uid in range(10)}
    teams = {uid: "A" if uid < 5 else "B" for uid in range(10)}
    deltas = compute_deltas([ratings[u] for u in range(5)], [ratings[u] for u in range(5, 10)], True, (13, 7))
    await db.apply_result(match_id, "valorant", "A", deltas, {u: r.rating for u, r in ratings.items()}, teams, (13, 7), 0)

    winner = await db.get_rating(0, "valorant")
    assert winner.wins == 1 and winner.rating > 1000
    assert await db.active_match_for(3) is None
    history = await db.history(0, "valorant")
    assert history[0]["map"] == "Ascent"
    assert (await db.captain_record(0, "valorant")) == (1, 1)

    await db.revert_result(match_id, "valorant")
    restored = await db.get_rating(0, "valorant")
    assert restored.games == 0 and abs(restored.rating - 1000) < 1e-9


async def test_leaderboard_orders(db):
    await db.seed_rating(1, "valorant", 1500, "Diamond 1")
    await db.seed_rating(2, "valorant", 900, None)
    rows = await db.leaderboard("valorant", "elo", min_games=0)
    assert [r.user_id for r in rows] == [1, 2]
