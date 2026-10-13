from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from .games import valorant_tier_to_rank

RIOT_ID = re.compile(r"^(?P<name>[^#]{3,16})#(?P<tag>[A-Za-z0-9]{3,5})$")
REGIONS = ("eu", "na", "ap", "kr", "latam", "br")
PEAK_DISCOUNT_TIERS = 3


class RankLookupError(Exception):
    pass


@dataclass
class ValorantRank:
    riot_id: str
    current: str | None
    peak: str | None
    current_tier: int
    peak_tier: int

    @property
    def seed_tier(self) -> int:
        """Current rank, unless the player hasn't played ranked recently, in which case
        a peak a few divisions down is a better guess than placing them at the bottom."""
        return max(self.current_tier, self.peak_tier - PEAK_DISCOUNT_TIERS if self.peak_tier >= 3 else 0)

    @property
    def seed_rank(self) -> str | None:
        return valorant_tier_to_rank(self.seed_tier)


def parse_riot_id(text: str) -> tuple[str, str]:
    m = RIOT_ID.match(text.strip())
    if not m:
        raise RankLookupError("That doesn't look like a Riot ID. Use the format `Name#TAG`, for example `Arsenic#9738`.")
    return m["name"].strip(), m["tag"]


def _tier(obj: dict | None) -> int:
    if not obj:
        return 0
    tier = obj.get("tier")
    if isinstance(tier, dict):
        return int(tier.get("id") or 0)
    if isinstance(tier, int):
        return tier
    return int(obj.get("currenttier") or 0)


def parse_mmr_response(riot_id: str, payload: dict) -> ValorantRank:
    """Accepts both the v3 (current/peak objects) and v2 (current_data/highest_rank) shapes."""
    data = payload.get("data") or {}
    current = _tier(data.get("current") or data.get("current_data"))
    peak = _tier(data.get("peak") or data.get("highest_rank"))
    peak = max(peak, current)
    return ValorantRank(riot_id, valorant_tier_to_rank(current), valorant_tier_to_rank(peak), current, peak)


class HenrikClient:
    """Unofficial Valorant API (https://api.henrikdev.xyz). Tracker.gg has no public
    Valorant API and blocks scraping, so this is the practical way to read a rank
    from a Riot ID."""

    BASE = "https://api.henrikdev.xyz/valorant"

    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _get(self, path: str) -> dict:
        if not self._session:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": self.api_key or ""}, timeout=aiohttp.ClientTimeout(total=10)
            )
        async with self._session.get(f"{self.BASE}{path}") as resp:
            if resp.status == 404:
                raise RankLookupError("Couldn't find that Riot ID. Check the spelling and the region.")
            if resp.status == 429:
                raise RankLookupError("The rank API is rate limiting us, try again in a minute.")
            if resp.status >= 400:
                raise RankLookupError(f"The rank API returned an error ({resp.status}).")
            return await resp.json()

    async def fetch_rank(self, riot_id: str, region: str) -> ValorantRank:
        if not self.enabled:
            raise RankLookupError("Rank lookups aren't configured on this bot. Ask staff to set your rank instead.")
        name, tag = parse_riot_id(riot_id)
        payload = await self._get(f"/v3/mmr/{region}/pc/{quote(name)}/{quote(tag)}")
        return parse_mmr_response(f"{name}#{tag}", payload)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
