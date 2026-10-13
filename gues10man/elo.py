from __future__ import annotations

from dataclasses import dataclass


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
