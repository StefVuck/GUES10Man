"""Finds the Riot custom game that corresponds to one of our matches.

Players are matched through the Riot IDs they linked with /link. A game is
accepted when it is a standard 5v5 that started after our match went live,
enough of our linked players appear in it, and each of our teams sits
entirely on one side of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .customs import CustomGame

CLOCK_SLACK = timedelta(minutes=10)
MIN_LINKED_PRESENT = 2
MIN_LINKED_SHARE = 0.6


@dataclass
class ImportedResult:
    game: CustomGame
    winner: str
    score: tuple[int, int]
    acs: dict[str, float]
    linked_present: int
    linked_total: int


def match_game(
    games: list[CustomGame],
    team_a: set[str],
    team_b: set[str],
    live_since: datetime | None,
) -> ImportedResult | None:
    """team_a and team_b are lowercased Riot IDs of our linked players on each side."""
    linked = len(team_a) + len(team_b)
    best: ImportedResult | None = None
    for game in games:
        if not game.is_standard or game.winner is None:
            continue
        if live_since and game.started_at and game.started_at < live_since - CLOCK_SLACK:
            continue
        side_of = {p.riot_id.lower(): p.team for p in game.players}
        sides_a = {side_of[r] for r in team_a if r in side_of}
        sides_b = {side_of[r] for r in team_b if r in side_of}
        present = sum(r in side_of for r in team_a | team_b)
        if present < MIN_LINKED_PRESENT or present < MIN_LINKED_SHARE * linked:
            continue
        if len(sides_a) > 1 or len(sides_b) > 1 or (sides_a and sides_a == sides_b):
            continue

        names = game.team_names
        if sides_a:
            side_a = next(iter(sides_a))
        else:
            side_a = next(t for t in names if t not in sides_b)
        side_b = next(t for t in names if t != side_a)

        result = ImportedResult(
            game=game,
            winner="A" if game.winner == side_a else "B",
            score=(game.rounds_won.get(side_a, 0), game.rounds_won.get(side_b, 0)),
            acs={p.riot_id.lower(): game.acs(p) for p in game.players if game.acs(p) is not None},
            linked_present=present,
            linked_total=linked,
        )
        if best is None or present > best.linked_present:
            best = result
    return best
