"""Glue between the bot and the team Bradley-Terry fit in elo5v5.

The fit's team strength is the sum of player ratings, so a 400 point gap
between team sums is 10:1 odds. Players see `BASE + team_size * fitted`,
which makes the gap between team *averages* read like ordinary Elo and keeps
rank seeds (60 per division) on a familiar scale.
"""
from __future__ import annotations

from dataclasses import dataclass

from .elo5v5 import fit
from .games import DEFAULT_RATING

BASE = DEFAULT_RATING
PRIOR = 1.0
PERF_WEIGHT = 0.3


@dataclass
class MatchRecord:
    winners: list[int]
    losers: list[int]
    scores: dict[int, float] | None = None


def refit(matches: list[MatchRecord], anchors: dict[int, float], team_size: int) -> dict[int, float]:
    """Returns the displayed rating for every anchored player. Players with no
    completed matches sit exactly on their anchor."""
    model_anchors = {p: (a - BASE) / team_size for p, a in anchors.items()}
    fitted = fit(
        [(m.winners, m.losers, 1.0, m.scores) for m in matches],
        prior=PRIOR,
        perf_weight=PERF_WEIGHT,
        anchors=model_anchors,
    )
    ratings = dict(anchors)
    ratings.update({p: BASE + team_size * r for p, r in fitted.items()})
    return ratings
