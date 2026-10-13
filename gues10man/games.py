from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RATING = 1000.0


@dataclass(frozen=True)
class Game:
    key: str
    name: str
    team_size: int
    maps: tuple[str, ...] = ()
    ranks: tuple[str, ...] = ()
    rank_base: float = 600.0
    rank_step: float = 60.0

    @property
    def lobby_size(self) -> int:
        return self.team_size * 2

    def rating_for_rank(self, rank: str) -> float | None:
        idx = self.rank_index(rank)
        if idx is None:
            return None
        return self.rank_base + idx * self.rank_step

    def rank_index(self, rank: str) -> int | None:
        wanted = rank.strip().lower()
        for i, name in enumerate(self.ranks):
            if name.lower() == wanted:
                return i
        return None

    def rank_for_rating(self, rating: float) -> str | None:
        if not self.ranks:
            return None
        idx = round((rating - self.rank_base) / self.rank_step)
        return self.ranks[max(0, min(len(self.ranks) - 1, idx))]


def _tiered(names: list[str], divisions: int = 3) -> list[str]:
    return [f"{n} {d}" for n in names for d in range(1, divisions + 1)]


VALORANT_RANKS = tuple(
    _tiered(["Iron", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Ascendant", "Immortal"])
    + ["Radiant"]
)

GAMES: dict[str, Game] = {
    g.key: g
    for g in [
        Game(
            key="valorant",
            name="Valorant",
            team_size=5,
            maps=(
                "Abyss", "Ascent", "Bind", "Breeze", "Corrode", "Fracture", "Haven",
                "Icebox", "Lotus", "Pearl", "Split", "Sunset",
            ),
            ranks=VALORANT_RANKS,
        ),
        Game(
            key="cs2",
            name="Counter-Strike 2",
            team_size=5,
            maps=("Ancient", "Anubis", "Dust II", "Inferno", "Mirage", "Nuke", "Train"),
        ),
        Game(key="lol", name="League of Legends", team_size=5, maps=("Summoner's Rift",)),
        Game(
            key="overwatch",
            name="Overwatch 2",
            team_size=5,
            ranks=tuple(
                _tiered(["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Master", "Grandmaster", "Champion"], 5)
                + ["Top 500"]
            ),
            rank_base=600.0,
            rank_step=35.0,
        ),
    ]
}


def get_game(key: str) -> Game:
    return GAMES[key]


def valorant_tier_to_rank(tier_id: int) -> str | None:
    """Riot competitive tier ids: 3 is Iron 1 through 27 for Radiant; 0-2 are unranked."""
    idx = tier_id - 3
    if 0 <= idx < len(VALORANT_RANKS):
        return VALORANT_RANKS[idx]
    return None
