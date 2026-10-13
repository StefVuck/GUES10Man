from __future__ import annotations

import time
from dataclasses import dataclass

import aiosqlite

from .elo import PlayerRating
from .rating import MatchRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    user_id INTEGER PRIMARY KEY,
    riot_id TEXT,
    region TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ratings (
    user_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    rating REAL NOT NULL,
    anchor REAL NOT NULL,
    games INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    seed_rank TEXT,
    peak_rating REAL NOT NULL,
    PRIMARY KEY (user_id, game)
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER PRIMARY KEY,
    staff_role_id INTEGER
);

CREATE TABLE IF NOT EXISTS queues (
    guild_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    PRIMARY KEY (guild_id, game)
);

CREATE TABLE IF NOT EXISTS queue_entries (
    guild_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at REAL NOT NULL,
    PRIMARY KEY (guild_id, game, user_id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT,
    map TEXT,
    channel_id INTEGER,
    message_id INTEGER,
    winner TEXT,
    score_a INTEGER,
    score_b INTEGER,
    reported_by INTEGER,
    created_at INTEGER NOT NULL,
    live_at INTEGER,
    finished_at INTEGER
);

CREATE TABLE IF NOT EXISTS match_players (
    match_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    team TEXT,
    is_captain INTEGER NOT NULL DEFAULT 0,
    pick_order INTEGER,
    rating_before REAL,
    rating_delta REAL,
    score REAL,
    PRIMARY KEY (match_id, user_id)
);

CREATE TABLE IF NOT EXISTS fake_players (
    user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_match_players_user ON match_players(user_id);
"""

ACTIVE_STATUSES = ("setup", "drafting", "voting", "live")


@dataclass
class Rating:
    user_id: int
    game: str
    rating: float
    anchor: float
    games: int
    wins: int
    losses: int
    seed_rank: str | None
    peak_rating: float

    def as_player(self) -> PlayerRating:
        return PlayerRating(self.user_id, self.rating, self.games)


@dataclass
class GuildConfig:
    guild_id: int
    staff_role_id: int | None = None


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self.conn.commit()

    async def _migrate(self) -> None:
        """Adds columns introduced after a database was first created."""
        added = {"matches": {"live_at": "INTEGER"}}
        for table, columns in added.items():
            async with self.db.execute(f"PRAGMA table_info({table})") as cur:
                existing = {r["name"] for r in await cur.fetchall()}
            for column, kind in columns.items():
                if column not in existing:
                    await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    async def fake_players(self) -> dict[int, str]:
        async with self.db.execute("SELECT user_id, name FROM fake_players") as cur:
            return {r["user_id"]: r["name"] for r in await cur.fetchall()}

    async def add_fake_players(self, players: dict[int, str]) -> None:
        await self.db.executemany(
            "INSERT OR REPLACE INTO fake_players (user_id, name) VALUES (?, ?)", list(players.items())
        )
        await self.db.commit()

    async def wipe_game(self, game: str) -> None:
        """Deletes every match, rating and queue for a game. Only used for the sandbox."""
        await self.db.execute(
            "DELETE FROM match_players WHERE match_id IN (SELECT id FROM matches WHERE game = ?)", (game,)
        )
        for table in ("matches", "ratings", "queues", "queue_entries"):
            await self.db.execute(f"DELETE FROM {table} WHERE game = ?", (game,))
        await self.db.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self.conn is not None, "database not connected"
        return self.conn

    # players

    async def ensure_player(self, user_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO players (user_id, created_at) VALUES (?, ?)", (user_id, int(time.time()))
        )

    async def set_riot_id(self, user_id: int, riot_id: str, region: str) -> None:
        await self.ensure_player(user_id)
        await self.db.execute("UPDATE players SET riot_id = ?, region = ? WHERE user_id = ?", (riot_id, region, user_id))
        await self.db.commit()

    async def users_by_riot_id(self, riot_ids: list[str]) -> dict[str, int]:
        """Maps lowercased Riot IDs to the Discord users who linked them."""
        if not riot_ids:
            return {}
        wanted = [r.lower() for r in riot_ids]
        async with self.db.execute(
            f"SELECT user_id, riot_id FROM players WHERE LOWER(riot_id) IN ({','.join('?' * len(wanted))})", wanted
        ) as cur:
            return {r["riot_id"].lower(): r["user_id"] for r in await cur.fetchall()}

    async def get_player(self, user_id: int) -> aiosqlite.Row | None:
        async with self.db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()

    # ratings

    async def get_rating(self, user_id: int, game: str) -> Rating | None:
        async with self.db.execute("SELECT * FROM ratings WHERE user_id = ? AND game = ?", (user_id, game)) as cur:
            row = await cur.fetchone()
        return Rating(**dict(row)) if row else None

    async def get_or_create_rating(self, user_id: int, game: str, default: float) -> Rating:
        existing = await self.get_rating(user_id, game)
        if existing:
            return existing
        await self.ensure_player(user_id)
        await self.db.execute(
            "INSERT INTO ratings (user_id, game, rating, anchor, peak_rating) VALUES (?, ?, ?, ?, ?)",
            (user_id, game, default, default, default),
        )
        await self.db.commit()
        return Rating(user_id, game, default, default, 0, 0, 0, None, default)

    async def seed_rating(self, user_id: int, game: str, anchor: float, seed_rank: str | None) -> None:
        """Sets where a player's prior is centred. Call a refit afterwards to update ratings."""
        await self.ensure_player(user_id)
        await self.db.execute(
            """INSERT INTO ratings (user_id, game, rating, anchor, seed_rank, peak_rating) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, game) DO UPDATE SET anchor = excluded.anchor, seed_rank = excluded.seed_rank""",
            (user_id, game, anchor, anchor, seed_rank, anchor),
        )
        await self.db.commit()

    async def fit_inputs(self, game: str) -> tuple[list[MatchRecord], dict[int, float]]:
        async with self.db.execute("SELECT user_id, anchor FROM ratings WHERE game = ?", (game,)) as cur:
            anchors = {r["user_id"]: r["anchor"] for r in await cur.fetchall()}
        async with self.db.execute(
            """SELECT m.id, m.winner, mp.user_id, mp.team, mp.score FROM matches m
               JOIN match_players mp ON mp.match_id = m.id
               WHERE m.game = ? AND m.status = 'complete' ORDER BY m.id""",
            (game,),
        ) as cur:
            rows = await cur.fetchall()
        records: dict[int, MatchRecord] = {}
        for r in rows:
            rec = records.setdefault(r["id"], MatchRecord([], []))
            (rec.winners if r["team"] == r["winner"] else rec.losers).append(r["user_id"])
            if r["score"] is not None:
                rec.scores = rec.scores or {}
                rec.scores[r["user_id"]] = r["score"]
        return list(records.values()), anchors

    async def current_ratings(self, game: str) -> dict[int, float]:
        async with self.db.execute("SELECT user_id, rating FROM ratings WHERE game = ?", (game,)) as cur:
            return {r["user_id"]: r["rating"] for r in await cur.fetchall()}

    async def write_ratings(self, game: str, ratings: dict[int, float]) -> None:
        await self.db.executemany(
            "UPDATE ratings SET rating = ?, peak_rating = MAX(peak_rating, ?) WHERE user_id = ? AND game = ?",
            [(r, r, uid, game) for uid, r in ratings.items()],
        )
        await self.db.commit()

    async def add_match_deltas(self, match_id: int, before: dict[int, float], after: dict[int, float]) -> None:
        """Credits a refit's rating movement to the match that triggered it."""
        for p in await self.match_players(match_id):
            uid = p["user_id"]
            await self.db.execute(
                """UPDATE match_players SET rating_before = COALESCE(rating_before, ?),
                   rating_delta = COALESCE(rating_delta, 0) + ? WHERE match_id = ? AND user_id = ?""",
                (before[uid], after[uid] - before[uid], match_id, uid),
            )
        await self.db.commit()

    async def set_scores(self, match_id: int, scores: dict[int, float]) -> None:
        await self.db.executemany(
            "UPDATE match_players SET score = ? WHERE match_id = ? AND user_id = ?",
            [(v, match_id, uid) for uid, v in scores.items()],
        )
        await self.db.commit()

    async def leaderboard(self, game: str, order: str, min_games: int, limit: int = 200) -> list[Rating]:
        column = {"elo": "rating", "wins": "wins", "winrate": "CAST(wins AS REAL) / games"}[order]
        async with self.db.execute(
            f"SELECT * FROM ratings WHERE game = ? AND games >= ? ORDER BY {column} DESC, rating DESC LIMIT ?",
            (game, min_games, limit),
        ) as cur:
            return [Rating(**dict(r)) for r in await cur.fetchall()]

    # guild config

    async def get_config(self, guild_id: int) -> GuildConfig:
        async with self.db.execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)) as cur:
            row = await cur.fetchone()
        return GuildConfig(**dict(row)) if row else GuildConfig(guild_id)

    async def update_config(self, guild_id: int, **fields: int | None) -> None:
        await self.db.execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,))
        for key, value in fields.items():
            if key not in GuildConfig.__dataclass_fields__ or key == "guild_id":
                raise ValueError(key)
            await self.db.execute(f"UPDATE guild_config SET {key} = ? WHERE guild_id = ?", (value, guild_id))
        await self.db.commit()

    # queues

    async def set_queue_message(self, guild_id: int, game: str, channel_id: int, message_id: int | None) -> None:
        await self.db.execute(
            """INSERT INTO queues (guild_id, game, channel_id, message_id) VALUES (?, ?, ?, ?)
               ON CONFLICT(guild_id, game) DO UPDATE SET channel_id = excluded.channel_id, message_id = excluded.message_id""",
            (guild_id, game, channel_id, message_id),
        )
        await self.db.commit()

    async def get_queue(self, guild_id: int, game: str) -> aiosqlite.Row | None:
        async with self.db.execute("SELECT * FROM queues WHERE guild_id = ? AND game = ?", (guild_id, game)) as cur:
            return await cur.fetchone()

    async def delete_queue(self, guild_id: int, game: str) -> None:
        await self.db.execute("DELETE FROM queues WHERE guild_id = ? AND game = ?", (guild_id, game))
        await self.db.execute("DELETE FROM queue_entries WHERE guild_id = ? AND game = ?", (guild_id, game))
        await self.db.commit()

    async def queue_members(self, guild_id: int, game: str) -> list[int]:
        async with self.db.execute(
            "SELECT user_id FROM queue_entries WHERE guild_id = ? AND game = ? ORDER BY joined_at",
            (guild_id, game),
        ) as cur:
            return [r["user_id"] for r in await cur.fetchall()]

    async def join_queue(self, guild_id: int, game: str, user_id: int) -> bool:
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO queue_entries (guild_id, game, user_id, joined_at) VALUES (?, ?, ?, ?)",
            (guild_id, game, user_id, time.time()),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def leave_queue(self, guild_id: int, game: str, user_id: int) -> bool:
        cur = await self.db.execute(
            "DELETE FROM queue_entries WHERE guild_id = ? AND game = ? AND user_id = ?", (guild_id, game, user_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def clear_queue(self, guild_id: int, game: str) -> None:
        await self.db.execute("DELETE FROM queue_entries WHERE guild_id = ? AND game = ?", (guild_id, game))
        await self.db.commit()

    # matches

    async def create_match(self, guild_id: int, game: str, user_ids: list[int]) -> int:
        cur = await self.db.execute(
            "INSERT INTO matches (guild_id, game, status, created_at) VALUES (?, ?, 'setup', ?)",
            (guild_id, game, int(time.time())),
        )
        match_id = cur.lastrowid
        await self.db.executemany(
            "INSERT INTO match_players (match_id, user_id) VALUES (?, ?)", [(match_id, u) for u in user_ids]
        )
        placeholders = ",".join("?" * len(user_ids))
        await self.db.execute(
            f"DELETE FROM queue_entries WHERE guild_id = ? AND game = ? AND user_id IN ({placeholders})",
            (guild_id, game, *user_ids),
        )
        await self.db.commit()
        return match_id

    async def get_match(self, match_id: int) -> aiosqlite.Row | None:
        async with self.db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)) as cur:
            return await cur.fetchone()

    async def match_players(self, match_id: int) -> list[aiosqlite.Row]:
        async with self.db.execute(
            "SELECT * FROM match_players WHERE match_id = ? ORDER BY team, is_captain DESC, pick_order", (match_id,)
        ) as cur:
            return list(await cur.fetchall())

    async def update_match(self, match_id: int, **fields) -> None:
        for key, value in fields.items():
            await self.db.execute(f"UPDATE matches SET {key} = ? WHERE id = ?", (value, match_id))
        await self.db.commit()

    async def set_teams(
        self,
        match_id: int,
        team_a: list[int],
        team_b: list[int],
        captains: tuple[int, int] | None,
        pick_order: dict[int, int] | None = None,
    ) -> None:
        pick_order = pick_order or {}
        for team, ids in (("A", team_a), ("B", team_b)):
            for uid in ids:
                await self.db.execute(
                    "UPDATE match_players SET team = ?, is_captain = ?, pick_order = ? WHERE match_id = ? AND user_id = ?",
                    (team, int(bool(captains and uid in captains)), pick_order.get(uid), match_id, uid),
                )
        await self.db.commit()

    async def swap_sides(self, match_id: int) -> None:
        await self.db.execute(
            "UPDATE match_players SET team = CASE team WHEN 'A' THEN 'B' ELSE 'A' END WHERE match_id = ?", (match_id,)
        )
        await self.db.commit()

    async def active_match_for(self, user_id: int) -> int | None:
        async with self.db.execute(
            f"""SELECT m.id FROM matches m JOIN match_players mp ON mp.match_id = m.id
                WHERE mp.user_id = ? AND m.status IN ({",".join("?" * len(ACTIVE_STATUSES))})""",
            (user_id, *ACTIVE_STATUSES),
        ) as cur:
            row = await cur.fetchone()
        return row["id"] if row else None

    async def active_matches(self) -> list[aiosqlite.Row]:
        async with self.db.execute(
            f"SELECT * FROM matches WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})", ACTIVE_STATUSES
        ) as cur:
            return list(await cur.fetchall())

    async def apply_result(
        self,
        match_id: int,
        game: str,
        winner: str,
        teams: dict[int, str],
        score: tuple[int, int] | None,
        reported_by: int,
    ) -> None:
        try:
            for uid, team in teams.items():
                won = team == winner
                await self.db.execute(
                    "UPDATE ratings SET games = games + 1, wins = wins + ?, losses = losses + ? WHERE user_id = ? AND game = ?",
                    (int(won), int(not won), uid, game),
                )
            await self.db.execute(
                """UPDATE matches SET status = 'complete', winner = ?, score_a = ?, score_b = ?, reported_by = ?,
                   finished_at = ? WHERE id = ?""",
                (winner, score[0] if score else None, score[1] if score else None, reported_by, int(time.time()), match_id),
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

    async def revert_result(self, match_id: int, game: str) -> None:
        """Puts a completed match back to live. Call a refit afterwards."""
        players = await self.match_players(match_id)
        match = await self.get_match(match_id)
        try:
            for p in players:
                won = p["team"] == match["winner"]
                await self.db.execute(
                    "UPDATE ratings SET games = games - 1, wins = wins - ?, losses = losses - ? WHERE user_id = ? AND game = ?",
                    (int(won), int(not won), p["user_id"], game),
                )
            await self.db.execute(
                "UPDATE match_players SET rating_before = NULL, rating_delta = NULL, score = NULL WHERE match_id = ?",
                (match_id,),
            )
            await self.db.execute(
                "UPDATE matches SET status = 'live', winner = NULL, score_a = NULL, score_b = NULL, finished_at = NULL WHERE id = ?",
                (match_id,),
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

    # history and profiles

    async def history(self, user_id: int, game: str, limit: int = 10) -> list[aiosqlite.Row]:
        async with self.db.execute(
            """SELECT m.id, m.map, m.winner, m.score_a, m.score_b, m.finished_at, mp.team, mp.rating_delta,
                      mp.rating_before, mp.is_captain, mp.score
               FROM match_players mp JOIN matches m ON m.id = mp.match_id
               WHERE mp.user_id = ? AND m.game = ? AND m.status = 'complete'
               ORDER BY m.finished_at DESC LIMIT ?""",
            (user_id, game, limit),
        ) as cur:
            return list(await cur.fetchall())

    async def map_stats(self, user_id: int, game: str) -> list[aiosqlite.Row]:
        async with self.db.execute(
            """SELECT m.map, COUNT(*) AS played, SUM(mp.team = m.winner) AS won
               FROM match_players mp JOIN matches m ON m.id = mp.match_id
               WHERE mp.user_id = ? AND m.game = ? AND m.status = 'complete' AND m.map IS NOT NULL
               GROUP BY m.map ORDER BY played DESC""",
            (user_id, game),
        ) as cur:
            return list(await cur.fetchall())

    async def teammate_stats(self, user_id: int, game: str, min_games: int = 3) -> list[aiosqlite.Row]:
        async with self.db.execute(
            """SELECT other.user_id, COUNT(*) AS played, SUM(me.team = m.winner) AS won
               FROM match_players me
               JOIN match_players other ON other.match_id = me.match_id AND other.team = me.team AND other.user_id != me.user_id
               JOIN matches m ON m.id = me.match_id
               WHERE me.user_id = ? AND m.game = ? AND m.status = 'complete'
               GROUP BY other.user_id HAVING played >= ?
               ORDER BY CAST(won AS REAL) / played DESC, played DESC""",
            (user_id, game, min_games),
        ) as cur:
            return list(await cur.fetchall())

    async def captain_record(self, user_id: int, game: str) -> tuple[int, int]:
        async with self.db.execute(
            """SELECT COUNT(*) AS played, COALESCE(SUM(mp.team = m.winner), 0) AS won
               FROM match_players mp JOIN matches m ON m.id = mp.match_id
               WHERE mp.user_id = ? AND m.game = ? AND m.status = 'complete' AND mp.is_captain = 1""",
            (user_id, game),
        ) as cur:
            row = await cur.fetchone()
        return row["played"], row["won"]
