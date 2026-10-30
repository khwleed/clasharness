"""Turn an opponent's name (and clan name) into a player tag.

The official API can't search players by name, so we try, cheapest first:
  1. our local player index (every player seen in any battle log we've fetched),
  2. clan search by clan name, then that clan's member list,
and otherwise hand the user a RoyaleAPI search link.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from urllib.parse import quote

from rapidfuzz import fuzz, process

from ..api import ApiError, ClashApi
from ..db import Database, norm_name

log = logging.getLogger(__name__)

NAME_CUTOFF = 85  # minimum fuzzy score to consider a name a candidate
CONFIDENT = 92  # minimum score to accept a single best candidate automatically
MARGIN = 5  # ...and how far ahead of the runner-up it must be


@dataclass
class Candidate:
    tag: str
    name: str
    clan_name: str | None
    trophies: int | None
    score: float
    source: str


@dataclass
class Resolution:
    status: str  # "resolved" | "ambiguous" | "not_found"
    tag: str | None
    candidates: list[Candidate] = field(default_factory=list)
    search_url: str = ""

    def to_dict(self) -> dict:
        return {**asdict(self), "candidates": [asdict(c) for c in self.candidates]}


def _score(name_norm: str, target: str) -> float:
    return fuzz.ratio(name_norm, target)


def _clan_bonus(cand_clan: str | None, clan: str) -> float:
    if not clan:
        return 0.0
    if not cand_clan:
        return -5.0
    return 6.0 if fuzz.ratio(norm_name(cand_clan), norm_name(clan)) >= 85 else -6.0


def search_local(db: Database, name: str, clan: str, my_trophies: int | None, limit: int = 10) -> list[Candidate]:
    target = norm_name(name)
    if not target:
        return []
    names = dict(db.all_player_names())
    hits = process.extract(target, names, scorer=fuzz.ratio, limit=limit, score_cutoff=NAME_CUTOFF)
    out = []
    for _, score, tag in hits:
        p = db.get_player(tag)
        bonus = _clan_bonus(p["clan_name"], clan)
        if my_trophies and p["trophies"]:
            bonus += 2.0 if abs(p["trophies"] - my_trophies) <= 600 else -2.0
        out.append(Candidate(tag, p["name"], p["clan_name"], p["trophies"], score + bonus, "index"))
    return out


async def search_clans(api: ClashApi, db: Database, name: str, clan: str, max_clans: int = 8) -> list[Candidate]:
    if not clan or not api.enabled:
        return []
    target, clan_norm = norm_name(name), norm_name(clan)
    try:
        clans = await api.search_clans(clan, limit=25)
    except ApiError as e:
        log.warning("clan search failed: %s", e)
        return []
    clans = sorted((c for c in clans if fuzz.ratio(norm_name(c["name"]), clan_norm) >= 90),
                   key=lambda c: -fuzz.ratio(norm_name(c["name"]), clan_norm))[:max_clans]
    out = []
    for c in clans:
        try:
            members = await api.clan_members(c["tag"])
        except ApiError as e:
            log.warning("members of %s failed: %s", c["tag"], e)
            continue
        for m in members:
            # Every member we see grows the index for next time.
            db.upsert_player({"tag": m["tag"], "name": m["name"], "clan_tag": c["tag"],
                              "clan_name": c["name"], "trophies": m.get("trophies")})
            s = _score(norm_name(m["name"]), target)
            if s >= NAME_CUTOFF:
                out.append(Candidate(m["tag"], m["name"], c["name"], m.get("trophies"), s + 6.0, "clan"))
    return out


def royaleapi_search_url(name: str, template: str) -> str:
    return template.format(q=quote(name))


def decide(cands: list[Candidate]) -> tuple[str, str | None, list[Candidate]]:
    merged: dict[str, Candidate] = {}
    for c in cands:
        if c.tag not in merged or c.score > merged[c.tag].score:
            merged[c.tag] = c
    ranked = sorted(merged.values(), key=lambda c: c.score, reverse=True)
    if not ranked:
        return "not_found", None, []
    top = ranked[0]
    runner = ranked[1].score if len(ranked) > 1 else 0
    if top.score >= CONFIDENT and top.score - runner >= MARGIN:
        return "resolved", top.tag, ranked[:6]
    return "ambiguous", None, ranked[:6]


async def resolve(api: ClashApi, db: Database, name: str, clan: str = "", my_trophies: int | None = None,
                  search_template: str = "https://royaleapi.com/player/search/results?q={q}") -> Resolution:
    cands = search_local(db, name, clan, my_trophies)
    status, tag, ranked = decide(cands)
    if status != "resolved":
        cands += await search_clans(api, db, name, clan)
        status, tag, ranked = decide(cands)
    return Resolution(status, tag, ranked, royaleapi_search_url(name, search_template))
