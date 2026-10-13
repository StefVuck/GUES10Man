from gues10man.elo import PlayerRating, win_probability


def test_win_probability_symmetric():
    a = [PlayerRating(i, 1200, 0) for i in range(5)]
    b = [PlayerRating(i + 5, 1000, 0) for i in range(5)]
    assert abs(win_probability(a, b) + win_probability(b, a) - 1) < 1e-9
