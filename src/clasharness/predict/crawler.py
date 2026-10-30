"""Build the deck dataset and player index by walking battle logs outward from you."""

from __future__ import annotations

import logging
from collections import deque
from typing import Callable

from ..api import ApiError, ClashApi, normalize_tag, parse_battle
from ..db import Database

log = logging.getLogger(__name__)


async def ingest_battlelog(api: ClashApi, db: Database, tag: str) -> list[dict]:
    """Fetch one player's battle log, store every ranked 1v1 battle. Returns the parsed battles."""
    raw = await api.battlelog(tag)
    battles = []
    for b in raw:
        parsed = parse_battle(b)
        if not parsed:
            continue
        if parsed["ranked"]:
            db.ingest_battle(parsed)
        else:  # still learn who the players are
            for side in parsed["sides"]:
                db.upsert_player(side, parsed["time"])
        battles.append(parsed)
    db.mark_crawled(normalize_tag(tag))
    return battles


async def crawl(api: ClashApi, db: Database, seeds: list[str], max_players: int = 200,
                trophy_center: int | None = None, trophy_window: int = 800,
                progress: Callable[[dict], None] | None = None) -> dict:
    """Breadth-first over opponents, staying near `trophy_center` when it's known."""
    queue = deque(normalize_tag(s) for s in seeds)
    visited: set[str] = set()
    stats = {"players": 0, "battles": 0, "errors": 0}
    while queue and stats["players"] < max_players:
        tag = queue.popleft()
        if tag in visited:
            continue
        visited.add(tag)
        try:
            battles = await ingest_battlelog(api, db, tag)
        except ApiError as e:
            stats["errors"] += 1
            log.warning("battlelog %s failed: %s", tag, e)
            if e.status in (0, 403):  # no token / IP not whitelisted: nothing else will work
                break
            continue
        stats["players"] += 1
        stats["battles"] += len(battles)
        for b in battles:
            if not b["ranked"]:
                continue
            for side in b["sides"]:
                t = side["tag"]
                if not t or t in visited:
                    continue
                trophies = side.get("trophies")
                if trophy_center is not None and trophies is not None and abs(trophies - trophy_center) > trophy_window:
                    continue
                queue.append(t)
        if progress:
            progress({**stats, "queued": len(queue)})
    return stats


async def seed_tags(api: ClashApi, db: Database, my_tag: str | None, center: int | None, window: int,
                    include_pol_top: bool = False) -> list[str]:
    seeds: list[str] = []
    if my_tag:
        seeds.append(my_tag)
    seeds += db.crawl_candidates(center, window, 50)
    if include_pol_top:
        try:
            seeds += [p["tag"] for p in await api.pol_top_players(100)]
        except ApiError as e:
            log.warning("Path of Legends rankings unavailable: %s", e)
    return list(dict.fromkeys(seeds))
