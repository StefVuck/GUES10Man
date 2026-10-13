from __future__ import annotations

import itertools
import random

from .elo import PlayerRating, team_average


def auto_balance(
    players: list[PlayerRating],
    tolerance: float = 15.0,
    rng: random.Random | None = None,
) -> tuple[list[PlayerRating], list[PlayerRating]]:
    """Split players into two equal teams with the closest average rating.

    Any split within `tolerance` Elo of the best is eligible and one is chosen
    at random, so the same ten people don't get identical teams every night.
    """
    if len(players) % 2:
        raise ValueError("need an even number of players")
    rng = rng or random.Random()
    size = len(players) // 2
    anchor, rest = players[0], players[1:]
    splits = []
    for combo in itertools.combinations(range(len(rest)), size - 1):
        chosen = set(combo)
        a = [anchor] + [rest[i] for i in combo]
        b = [rest[i] for i in range(len(rest)) if i not in chosen]
        splits.append((abs(team_average(a) - team_average(b)), a, b))
    best = min(s[0] for s in splits)
    candidates = [s for s in splits if s[0] <= best + tolerance]
    _, a, b = rng.choice(candidates)
    if rng.random() < 0.5:
        a, b = b, a
    return a, b


def snake_order(picks: int) -> list[int]:
    """Pick order for a captains draft: 0 = first captain, 1 = second. A B B A A B B A ..."""
    order = []
    for i in range(picks):
        order.append(0 if (i % 4) in (0, 3) else 1)
    return order


def choose_captains(
    players: list[PlayerRating], method: str, rng: random.Random | None = None
) -> tuple[PlayerRating, PlayerRating]:
    """Returns (first_pick, second_pick). The lower rated captain picks first."""
    rng = rng or random.Random()
    if method == "random":
        c1, c2 = rng.sample(players, 2)
    else:
        ranked = sorted(players, key=lambda p: p.rating, reverse=True)
        c1, c2 = ranked[0], ranked[1]
    return (c1, c2) if c1.rating <= c2.rating else (c2, c1)
