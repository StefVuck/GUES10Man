import random

import pytest

from gues10man.balance import auto_balance, choose_captains, snake_order
from gues10man.elo import PlayerRating, team_average


def players(ratings):
    return [PlayerRating(i, r, 0) for i, r in enumerate(ratings)]


def test_auto_balance_finds_close_split():
    ps = players([2000, 1800, 1500, 1400, 1300, 1200, 1100, 1000, 900, 600])
    a, b = auto_balance(ps, tolerance=0, rng=random.Random(1))
    assert len(a) == len(b) == 5
    assert {p.user_id for p in a} | {p.user_id for p in b} == set(range(10))
    assert abs(team_average(a) - team_average(b)) <= 10


def test_auto_balance_varies_with_tolerance():
    ps = players([1000 + i * 10 for i in range(10)])
    splits = {frozenset(p.user_id for p in auto_balance(ps, tolerance=30, rng=random.Random(s))[0]) for s in range(30)}
    assert len(splits) > 2


def test_odd_players_rejected():
    with pytest.raises(ValueError):
        auto_balance(players([1000] * 9))


def test_snake_order():
    assert snake_order(8) == [0, 1, 1, 0, 0, 1, 1, 0]


def test_lower_captain_picks_first():
    first, second = choose_captains(players([1000, 1500, 1400, 900]), "top")
    assert (first.user_id, second.user_id) == (2, 1)
