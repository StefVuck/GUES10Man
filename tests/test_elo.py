from gues10man.elo import PlayerRating, compute_deltas, k_factor, margin_multiplier, win_probability


def team(ratings, start=0, games=20):
    return [PlayerRating(start + i, r, games) for i, r in enumerate(ratings)]


def test_even_match_is_zero_sum():
    a, b = team([1000] * 5), team([1000] * 5, start=10)
    deltas = compute_deltas(a, b, a_won=True)
    assert abs(sum(deltas.values())) < 1e-9
    assert all(deltas[p.user_id] > 0 for p in a)
    assert all(deltas[p.user_id] < 0 for p in b)


def test_upset_pays_more_than_expected_win():
    strong, weak = team([1400] * 5), team([1000] * 5, start=10)
    upset = compute_deltas(strong, weak, a_won=False)
    expected = compute_deltas(strong, weak, a_won=True)
    assert upset[10] > expected[0]


def test_carry_gains_less_than_teammates():
    a = team([1600, 1000, 1000, 1000, 1000])
    b = team([1120] * 5, start=10)
    deltas = compute_deltas(a, b, a_won=True)
    assert deltas[0] < deltas[1]


def test_new_players_move_faster():
    assert k_factor(0) > k_factor(5) > k_factor(10) == k_factor(50)


def test_margin_multiplier_bounds():
    assert margin_multiplier(None, None) == 1.0
    assert margin_multiplier(13, 11) < margin_multiplier(13, 3)
    assert margin_multiplier(13, 0) <= 1.5


def test_win_probability_symmetric():
    a, b = team([1200] * 5), team([1000] * 5, start=10)
    assert abs(win_probability(a, b) + win_probability(b, a) - 1) < 1e-9
