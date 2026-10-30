"""Official Clash Royale API client (usually via the RoyaleAPI proxy)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .cards import slugify

# Battle types we learn decks from: ladder (Trophy Road) and Path of Legends.
RANKED_TYPES = {"PvP", "pathOfLegend"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status


def normalize_tag(tag: str) -> str:
    tag = tag.strip().upper().replace("O", "0")
    return tag if tag.startswith("#") else "#" + tag


class ClashApi:
    def __init__(self, token: str, base_url: str, requests_per_second: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.enabled = bool(token)
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=15.0,
            transport=transport,
        )
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict | None = None):
        if not self.enabled:
            raise ApiError(0, "no API token configured (set [api].token in config.toml)")
        async with self._lock:  # simple client-side rate limit
            wait = self._next_slot - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_slot = time.monotonic() + self._interval
        r = await self.client.get(path, params=params)
        if r.status_code != 200:
            try:
                msg = r.json().get("reason") or r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            raise ApiError(r.status_code, msg)
        return r.json()

    @staticmethod
    def _tag(tag: str) -> str:
        return quote(normalize_tag(tag), safe="")

    async def cards(self) -> dict:
        return await self._get("/cards")

    async def player(self, tag: str) -> dict:
        return await self._get(f"/players/{self._tag(tag)}")

    async def battlelog(self, tag: str) -> list[dict]:
        return await self._get(f"/players/{self._tag(tag)}/battlelog")

    async def search_clans(self, name: str, limit: int = 20) -> list[dict]:
        if len(name.strip()) < 3:  # API rejects shorter names
            return []
        return (await self._get("/clans", {"name": name, "limit": limit})).get("items", [])

    async def clan_members(self, clan_tag: str) -> list[dict]:
        return (await self._get(f"/clans/{self._tag(clan_tag)}/members")).get("items", [])

    async def pol_top_players(self, limit: int = 200) -> list[dict]:
        return (await self._get("/locations/global/pathoflegend/players", {"limit": limit})).get("items", [])


def deck_from_player(player: dict) -> list[str]:
    return [slugify(c["name"]) for c in player.get("currentDeck", [])]


def _side(p: dict) -> dict:
    clan = p.get("clan") or {}
    return {
        "tag": p.get("tag"),
        "name": p.get("name"),
        "clan_tag": clan.get("tag"),
        "clan_name": clan.get("name"),
        "trophies": p.get("startingTrophies"),
        "crowns": p.get("crowns", 0),
        "cards": [slugify(c["name"]) for c in p.get("cards", [])],
    }


def parse_battle(b: dict) -> dict | None:
    """Normalize a battlelog entry; None for 2v2 and anything that isn't 1v1."""
    team, opp = b.get("team") or [], b.get("opponent") or []
    if len(team) != 1 or len(opp) != 1:
        return None
    me, them = _side(team[0]), _side(opp[0])
    tags = sorted([me["tag"] or "", them["tag"] or ""])
    return {
        "id": f"{b.get('battleTime')}|{tags[0]}|{tags[1]}",
        "time": b.get("battleTime", ""),
        "type": b.get("type"),
        "mode": (b.get("gameMode") or {}).get("name"),
        "ranked": b.get("type") in RANKED_TYPES,
        "sides": [me, them],
    }


def parse_battle_time(s: str) -> float:
    """'20261030T151200.000Z' -> unix seconds."""
    return datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
