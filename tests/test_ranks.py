import pytest

from gues10man.games import GAMES, valorant_tier_to_rank
from gues10man.ranks import RankLookupError, parse_mmr_response, parse_riot_id


def test_parse_riot_id():
    assert parse_riot_id("Arsenic#9738") == ("Arsenic", "9738")
    assert parse_riot_id("  some name#EUW ") == ("some name", "EUW")
    with pytest.raises(RankLookupError):
        parse_riot_id("no tag")


def test_tier_mapping():
    assert valorant_tier_to_rank(3) == "Iron 1"
    assert valorant_tier_to_rank(27) == "Radiant"
    assert valorant_tier_to_rank(0) is None


def test_v3_payload_seeds_from_current():
    payload = {"data": {"current": {"tier": {"id": 15, "name": "Platinum 1"}}, "peak": {"tier": {"id": 16}}}}
    rank = parse_mmr_response("A#1", payload)
    assert rank.current == "Platinum 1"
    assert rank.seed_rank == "Platinum 1"


def test_lapsed_player_seeds_from_discounted_peak():
    payload = {"data": {"current": {"tier": {"id": 0}}, "peak": {"tier": {"id": 21}}}}
    assert parse_mmr_response("A#1", payload).seed_rank == "Diamond 1"


def test_v2_payload():
    payload = {"data": {"current_data": {"currenttier": 12}, "highest_rank": {"tier": 14}}}
    assert parse_mmr_response("A#1", payload).seed_rank == "Gold 1"


def test_rank_ratings_are_monotonic():
    val = GAMES["valorant"]
    ratings = [val.rating_for_rank(r) for r in val.ranks]
    assert ratings == sorted(ratings)
    assert val.rank_for_rating(val.rating_for_rank("Gold 2")) == "Gold 2"
