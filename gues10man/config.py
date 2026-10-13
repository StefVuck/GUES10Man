from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    guild_ids: tuple[int, ...]
    henrik_api_key: str | None
    valorant_region: str
    database_path: str
    staff_user_ids: frozenset[int] = frozenset()

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
        return cls(
            token=token,
            guild_ids=tuple(int(x) for x in os.getenv("GUILD_ID", "").replace(",", " ").split()),
            henrik_api_key=os.getenv("HENRIK_API_KEY") or None,
            valorant_region=os.getenv("VALORANT_REGION", "eu"),
            database_path=os.getenv("DATABASE_PATH", "gues10man.db"),
            staff_user_ids=frozenset(int(x) for x in os.getenv("STAFF_USER_IDS", "").replace(",", " ").split()),
        )
