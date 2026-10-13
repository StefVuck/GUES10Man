import random

from gues10man.elo import PlayerRating, win_probability
from gues10man.elo5v5 import win_prob
from gues10man.rating import BASE, MatchRecord, refit

A, B = [0, 1, 2, 3, 4], [5, 6, 7, 8, 9]


def anchors(value=BASE, n=10):
    return {p: value for p in range(n)}


def test_no_matches_returns_anchors():
    seeds = {0: 1140.0, 1: 1680.0}
    assert refit([], seeds, 5) == seeds


def test_winners_rise_losers_fall():
    out = refit([MatchRecord(A, B)], anchors(), 5)
    assert all(out[p] > BASE for p in A)
    assert all(out[p] < BASE for p in B)


def test_unplayed_players_keep_anchor():
    seeds = anchors() | {42: 1500.0}
    assert refit([MatchRecord(A, B)], seeds, 5)[42] == 1500.0


def test_display_scale_matches_model_win_prob():
    out = refit([MatchRecord(A, B), MatchRecord(A[:3] + B[:2], A[3:] + B[2:])], anchors(), 5)
    model = {p: (r - BASE) / 5 for p, r in out.items()}
    shown = win_probability([PlayerRating(p, out[p], 0) for p in A], [PlayerRating(p, out[p], 0) for p in B])
    assert abs(shown - win_prob(model, A, B)) < 1e-9


def test_higher_seed_stays_ahead_after_one_game():
    seeds = anchors() | {0: 1600.0, 5: 700.0}
    out = refit([MatchRecord(B, A)], seeds, 5)
    assert out[0] > out[5]


def test_scoreboard_separates_teammates():
    scores = {p: 300 - 20 * i for i, p in enumerate(A)} | {p: 200 - 20 * i for i, p in enumerate(B)}
    out = refit([MatchRecord(A, B, scores)], anchors(), 5)
    assert out[0] > out[4] and out[5] > out[9]


def test_consistent_winner_tops_the_table():
    rng = random.Random(7)
    players = list(range(20))
    matches = []
    for _ in range(80):
        lobby = rng.sample(players, 10)
        a, b = lobby[:5], lobby[5:]
        if 0 in b or (0 not in a and rng.random() < 0.5):
            a, b = b, a
        matches.append(MatchRecord(a, b) if 0 in a or rng.random() < 0.5 else MatchRecord(b, a))
    out = refit(matches, anchors(n=20), 5)
    assert max(out, key=out.get) == 0
