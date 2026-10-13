"""Parsing for Valorant match history from the HenrikDev API.

The v4 and v3 endpoints lay matches out differently, and neither could be
checked against live data when this was written, so every field is read
defensively. `json_shape` prints a payload's structure without its values,
which is the quickest way to see what changed if parsing comes back empty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CustomPlayer:
    name: str
    tag: str
    team: str
    agent: str | None
    score: int
    kills: int
    deaths: int
    assists: int

    @property
    def riot_id(self) -> str:
        return f"{self.name}#{self.tag}"


@dataclass
class CustomGame:
    match_id: str | None
    map: str | None
    mode: str | None
    started_at: datetime | None
    rounds: int
    rounds_won: dict[str, int] = field(default_factory=dict)
    winner: str | None = None
    players: list[CustomPlayer] = field(default_factory=list)

    def acs(self, player: CustomPlayer) -> float | None:
        # Deathmatch-style customs report one "round" with kills as the score.
        return player.score / self.rounds if self.rounds > 1 else None

    @property
    def team_sizes(self) -> list[int]:
        return [len(self.team(t)) for t in self.team_names]

    @property
    def kind(self) -> str:
        if self.rounds <= 1:
            return "deathmatch"
        return "v".join(map(str, self.team_sizes)) or "unknown"

    @property
    def is_standard(self) -> bool:
        """A proper 5v5 that could count towards ratings."""
        return self.kind == "5v5" and len(self.team_names) == 2

    def team(self, name: str) -> list[CustomPlayer]:
        return sorted((p for p in self.players if p.team == name), key=lambda p: p.score, reverse=True)

    @property
    def team_names(self) -> list[str]:
        return sorted({p.team for p in self.players} | set(self.rounds_won))


def _name(value) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    return value


def _team_key(value) -> str:
    return str(value or "?").capitalize()


def _time(meta: dict) -> datetime | None:
    started = meta.get("started_at")
    if isinstance(started, str):
        try:
            return datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            return None
    start = meta.get("game_start")
    if isinstance(start, (int, float)):
        return datetime.fromtimestamp(start, tz=timezone.utc)
    return None


def parse_match(m: dict) -> CustomGame:
    meta = m.get("metadata") or {}
    raw_players = m.get("players") or []
    if isinstance(raw_players, dict):
        raw_players = raw_players.get("all_players") or []

    players = []
    for p in raw_players:
        stats = p.get("stats") or {}
        players.append(
            CustomPlayer(
                name=p.get("name") or "?",
                tag=p.get("tag") or "?",
                team=_team_key(p.get("team_id") or p.get("team")),
                agent=_name(p.get("agent")) or p.get("character"),
                score=int(stats.get("score") or 0),
                kills=int(stats.get("kills") or 0),
                deaths=int(stats.get("deaths") or 0),
                assists=int(stats.get("assists") or 0),
            )
        )

    rounds_won: dict[str, int] = {}
    winner = None
    teams = m.get("teams") or {}
    if isinstance(teams, list):
        for t in teams:
            key = _team_key(t.get("team_id"))
            rounds_won[key] = int((t.get("rounds") or {}).get("won") or 0)
            if t.get("won"):
                winner = key
    elif isinstance(teams, dict):
        for key, t in teams.items():
            if not isinstance(t, dict):
                continue
            rounds_won[_team_key(key)] = int(t.get("rounds_won") or 0)
            if t.get("has_won"):
                winner = _team_key(key)

    rounds = int(meta.get("rounds_played") or len(m.get("rounds") or []) or sum(rounds_won.values()))
    return CustomGame(
        match_id=meta.get("match_id") or meta.get("matchid"),
        map=_name(meta.get("map")),
        mode=_name(meta.get("queue")) or meta.get("mode"),
        started_at=_time(meta),
        rounds=rounds,
        rounds_won=rounds_won,
        winner=winner,
        players=players,
    )


def parse_matches(payload: dict) -> list[CustomGame]:
    data = payload.get("data") or []
    if isinstance(data, dict):
        data = data.get("matches") or [data]
    return [parse_match(m) for m in data if isinstance(m, dict)]


def json_shape(value, depth: int = 0, max_depth: int = 6) -> str:
    """Keys and types only, so a payload can be shared without anyone's names or stats."""
    pad = "  " * depth
    if depth >= max_depth:
        return f"{pad}..."
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(json_shape(v, depth + 1, max_depth))
            else:
                lines.append(f"{pad}{k}: {type(v).__name__}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{pad}[]"
        return f"{pad}[{len(value)} items, first:]\n" + json_shape(value[0], depth + 1, max_depth)
    return f"{pad}{type(value).__name__}"
