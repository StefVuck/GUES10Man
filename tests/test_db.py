import pytest

from gues10man.db import Database
from gues10man.rating import refit


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


async def do_refit(db, credit=None):
    matches, anchors = await db.fit_inputs("valorant")
    before = await db.current_ratings("valorant")
    after = refit(matches, anchors, 5)
    await db.write_ratings("valorant", after)
    if credit:
        await db.add_match_deltas(credit, before, after)


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
    teams = {uid: "A" if uid < 5 else "B" for uid in range(10)}
    await db.apply_result(match_id, "valorant", "A", teams, (13, 7), 0)
    await do_refit(db, match_id)

    winner = await db.get_rating(0, "valorant")
    assert winner.wins == 1 and winner.rating > 1000
    assert await db.active_match_for(3) is None
    history = await db.history(0, "valorant")
    assert history[0]["map"] == "Ascent"
    assert (await db.captain_record(0, "valorant")) == (1, 1)

    assert history[0]["rating_delta"] > 0

    await db.set_scores(match_id, {0: 310, 1: 120})
    matches, _ = await db.fit_inputs("valorant")
    assert matches[0].scores == {0: 310, 1: 120}
    await do_refit(db, match_id)
    assert (await db.get_rating(0, "valorant")).rating > (await db.get_rating(1, "valorant")).rating

    await db.revert_result(match_id, "valorant")
    await do_refit(db)
    restored = await db.get_rating(0, "valorant")
    assert restored.games == 0 and abs(restored.rating - 1000) < 1e-9


async def test_leaderboard_orders(db):
    await db.seed_rating(1, "valorant", 1500, "Diamond 1")
    await db.seed_rating(2, "valorant", 900, None)
    await do_refit(db)
    rows = await db.leaderboard("valorant", "elo", min_games=0)
    assert [r.user_id for r in rows] == [1, 2]


async def test_users_by_riot_id(db):
    await db.set_riot_id(7, "User#1234", "eu")
    assert await db.users_by_riot_id(["user#1234", "Nobody#000"]) == {"user#1234": 7}
