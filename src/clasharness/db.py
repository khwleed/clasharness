"""SQLite storage: deck dataset, player index, and saved matches."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from rapidfuzz import utils

SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    deck_key TEXT PRIMARY KEY,      -- 8 card slugs, sorted, comma-joined
    count INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT NOT NULL          -- battleTime of the latest sighting (ISO-ish, sortable)
);
CREATE TABLE IF NOT EXISTS battles (
    battle_id TEXT PRIMARY KEY,
    battle_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
    tag TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    clan_tag TEXT,
    clan_name TEXT,
    trophies INTEGER,
    last_seen TEXT,
    crawled_at REAL
);
CREATE INDEX IF NOT EXISTS players_name_norm ON players(name_norm);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    my_tag TEXT,
    my_deck TEXT NOT NULL,
    opp_tag TEXT,
    opp_name TEXT,
    opp_revealed TEXT,
    opp_deck TEXT,
    result TEXT,
    crowns TEXT,
    battle_time TEXT
);
CREATE TABLE IF NOT EXISTS match_events (
    match_id INTEGER NOT NULL REFERENCES matches(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    t REAL NOT NULL,
    side TEXT,
    card TEXT,
    amount REAL,
    PRIMARY KEY (match_id, seq)
);
"""


def deck_key(cards) -> str:
    return ",".join(sorted(cards))


def norm_name(name: str) -> str:
    return utils.default_process(name or "")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        self.conn.close()

    # ---- battles / decks / players -------------------------------------
    def ingest_battle(self, battle: dict) -> bool:
        """battle: output of api.parse_battle. Returns False if already seen."""
        with self.lock, self.conn:
            for side in battle["sides"]:
                self._upsert_player(side, battle["time"])
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO battles(battle_id, battle_time) VALUES (?, ?)",
                (battle["id"], battle["time"]),
            )
            if cur.rowcount == 0:
                return False
            for side in battle["sides"]:
                if len(side["cards"]) == 8:
                    self.conn.execute(
                        "INSERT INTO decks(deck_key, count, last_seen) VALUES (?, 1, ?) "
                        "ON CONFLICT(deck_key) DO UPDATE SET count = count + 1, "
                        "last_seen = MAX(last_seen, excluded.last_seen)",
                        (deck_key(side["cards"]), battle["time"]),
                    )
            return True

    def upsert_player(self, p: dict, seen: str | None = None) -> None:
        with self.lock, self.conn:
            self._upsert_player(p, seen)

    def _upsert_player(self, p: dict, seen: str | None) -> None:
        if not p.get("tag") or not p.get("name"):
            return
        self.conn.execute(
            "INSERT INTO players(tag, name, name_norm, clan_tag, clan_name, trophies, last_seen) "
            "VALUES (:tag, :name, :norm, :clan_tag, :clan_name, :trophies, :seen) "
            "ON CONFLICT(tag) DO UPDATE SET name = excluded.name, name_norm = excluded.name_norm, "
            "clan_tag = COALESCE(excluded.clan_tag, players.clan_tag), "
            "clan_name = COALESCE(excluded.clan_name, players.clan_name), "
            "trophies = COALESCE(excluded.trophies, players.trophies), "
            "last_seen = MAX(COALESCE(players.last_seen, ''), COALESCE(excluded.last_seen, ''))",
            {
                "tag": p["tag"], "name": p["name"], "norm": norm_name(p["name"]),
                "clan_tag": p.get("clan_tag"), "clan_name": p.get("clan_name"),
                "trophies": p.get("trophies"), "seen": seen,
            },
        )

    def mark_crawled(self, tag: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE players SET crawled_at = ? WHERE tag = ?", (time.time(), tag))

    def crawl_candidates(self, center: int | None, window: int, limit: int) -> list[str]:
        q = "SELECT tag FROM players"
        args: list = []
        if center is not None:
            q += " WHERE trophies BETWEEN ? AND ?"
            args += [center - window, center + window]
        q += " ORDER BY crawled_at IS NOT NULL, crawled_at, last_seen DESC LIMIT ?"
        with self.lock:
            return [r["tag"] for r in self.conn.execute(q, [*args, limit])]

    def all_player_names(self) -> list[tuple[str, str]]:
        with self.lock:
            return [(r["tag"], r["name_norm"]) for r in self.conn.execute("SELECT tag, name_norm FROM players")]

    def get_player(self, tag: str) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM players WHERE tag = ?", (tag,)).fetchone()
        return dict(r) if r else None

    def load_decks(self, since: str | None = None) -> list[tuple[str, int]]:
        q, args = "SELECT deck_key, count FROM decks", []
        if since:
            q += " WHERE last_seen >= ?"
            args.append(since)
        with self.lock:
            return [(r["deck_key"], r["count"]) for r in self.conn.execute(q, args)]

    def stats(self) -> dict:
        with self.lock:
            one = lambda q: self.conn.execute(q).fetchone()[0]  # noqa: E731
            return {
                "decks": one("SELECT COUNT(*) FROM decks"),
                "battles": one("SELECT COUNT(*) FROM battles"),
                "players": one("SELECT COUNT(*) FROM players"),
                "matches": one("SELECT COUNT(*) FROM matches"),
            }

    # ---- matches -------------------------------------------------------
    def save_match(self, match, my_tag: str | None, match_id: int | None = None) -> int:
        events = match.events
        row = {
            "started_at": match.start_t, "ended_at": match.end_t, "my_tag": my_tag,
            "my_deck": json.dumps(match.my_deck), "opp_tag": match.opp_tag, "opp_name": match.opp_name,
            "opp_revealed": json.dumps(match.opp_revealed()),
        }
        with self.lock, self.conn:
            if match_id is None:
                cur = self.conn.execute(
                    "INSERT INTO matches(started_at, ended_at, my_tag, my_deck, opp_tag, opp_name, opp_revealed) "
                    "VALUES (:started_at, :ended_at, :my_tag, :my_deck, :opp_tag, :opp_name, :opp_revealed)", row)
                match_id = cur.lastrowid
            else:
                self.conn.execute(
                    "UPDATE matches SET ended_at=:ended_at, opp_tag=:opp_tag, opp_name=:opp_name, "
                    "opp_revealed=:opp_revealed WHERE id=:id", {**row, "id": match_id})
                self.conn.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
            self.conn.executemany(
                "INSERT INTO match_events(match_id, seq, type, t, side, card, amount) VALUES (?,?,?,?,?,?,?)",
                [(match_id, i, e.type, e.t, e.side, e.card, e.amount) for i, e in enumerate(events)],
            )
        return match_id

    def reconcile_match(self, match_id: int, *, opp_tag: str, opp_name: str, opp_deck: list[str],
                        result: str, crowns: list[int], battle_time: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE matches SET opp_tag=?, opp_name=?, opp_deck=?, result=?, crowns=?, battle_time=? WHERE id=?",
                (opp_tag, opp_name, json.dumps(opp_deck), result, json.dumps(crowns), battle_time, match_id),
            )

    def recent_matches(self, limit: int = 20) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM matches ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("my_deck", "opp_revealed", "opp_deck", "crowns"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out

    def match_events(self, match_id: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM match_events WHERE match_id = ? ORDER BY seq", (match_id,))
            return [dict(r) for r in rows]
