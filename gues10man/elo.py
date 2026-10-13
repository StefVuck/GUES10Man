from __future__ import annotations

import math
from dataclasses import dataclass

PROVISIONAL_GAMES = 10
K_PROVISIONAL = 48.0
K_ESTABLISHED = 28.0
SELF_WEIGHT = 0.35


@dataclass
class PlayerRating:
    user_id: int
    rating: float
    games: int


def expected(a: float, b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))


def team_average(team: list[PlayerRating]) -> float:
    return sum(p.rating for p in team) / len(team)


def win_probability(team_a: list[PlayerRating], team_b: list[PlayerRating]) -> float:
    return expected(team_average(team_a), team_average(team_b))


def margin_multiplier(rounds_won: int | None, rounds_lost: int | None) -> float:
    """A stomp moves ratings a little more than a 13-11 nailbiter, capped at 1.5x."""
    if rounds_won is None or rounds_lost is None:
        return 1.0
    diff = abs(rounds_won - rounds_lost)
    return min(1.5, 1.0 + math.log1p(max(0, diff - 1)) / 5.0)


def k_factor(games: int) -> float:
    if games < PROVISIONAL_GAMES:
        return K_PROVISIONAL - (K_PROVISIONAL - K_ESTABLISHED) * games / PROVISIONAL_GAMES
    return K_ESTABLISHED


def compute_deltas(
    team_a: list[PlayerRating],
    team_b: list[PlayerRating],
    a_won: bool,
    score: tuple[int, int] | None = None,
) -> dict[int, float]:
    """Team Elo with a small individual component.

    Each player's effective strength blends their own rating with their team's
    average, so a carry on a weak team gains less than the rest of that team
    and new players settle quickly thanks to a decaying K factor.
    """
    avg_a, avg_b = team_average(team_a), team_average(team_b)
    mult = margin_multiplier(*score) if score else 1.0
    deltas: dict[int, float] = {}
    for team, own_avg, opp_avg, won in ((team_a, avg_a, avg_b, a_won), (team_b, avg_b, avg_a, not a_won)):
        for p in team:
            strength = (1 - SELF_WEIGHT) * own_avg + SELF_WEIGHT * p.rating
            exp = expected(strength, opp_avg)
            deltas[p.user_id] = k_factor(p.games) * mult * ((1.0 if won else 0.0) - exp)
    return deltas
