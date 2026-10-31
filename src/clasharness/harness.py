"""The harness: owns the current match and every service around it.

All inputs (hotkeys, browser clicks, the search popup) go through `dispatch()`,
which is thread-safe. Slow work (API calls, vision) runs as tasks on the
server's event loop and bumps `version` when it changes what the UI shows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone

from .api import ApiError, ClashApi, deck_from_player, normalize_tag, parse_battle, parse_battle_time
from .cards import Catalog
from .config import Config
from .db import Database
from .match import cycle
from .match.elixir import Rules
from .match.state import SIDES, Match
from .predict.crawler import crawl, ingest_battlelog, seed_tags
from .predict.model import DeckIndex, Prediction, predict
from .scrcpy import Scrcpy

log = logging.getLogger(__name__)


class CommandError(Exception):
    pass


class Harness:
    def __init__(self, config: Config, api: ClashApi | None = None):
        self.cfg = config
        self.var = config.storage_dir
        self.db = Database(self.var / "clasharness.db")
        self.api = api or ClashApi(config["api"]["token"], config["api"]["base_url"], config["api"]["requests_per_second"])
        self.catalog = Catalog.load(self.var / "cards.json")
        self.rules = Rules.from_config(config["elixir"])
        self.scrcpy = Scrcpy(config["scrcpy"]["path"], config["scrcpy"]["args"])
        self.lock = threading.RLock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.version = 0
        self.messages: list[dict] = []  # recent status/error lines for the UI

        state = self._load_state()
        self.my_tag: str | None = state.get("my_tag") or config["api"]["my_tag"] or None
        self.my_deck: list[str] = state.get("my_deck", [])
        self.my_trophies: int | None = state.get("my_trophies")
        self.coach: bool = bool(state.get("coach", False))

        self.match: Match | None = None
        self.match_id: int | None = None
        self.meta = DeckIndex([])
        self.opp_history: list[list[str]] = []
        self.opp_history_status = ""
        self.prediction = Prediction([], [], "none", 0)
        self.vision: dict = {"status": "idle"}
        self.crawl_status: dict = {"running": False}
        self._tasks: set[asyncio.Task] = set()
        self._stats_cache: tuple[float, dict] = (0.0, {})

    # ---- lifecycle -----------------------------------------------------
    async def startup(self) -> None:
        self.loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.reload_meta)
        if self.api.enabled:
            self.spawn(self.refresh_cards())
            if self.my_tag:
                self.spawn(self.refresh_my_deck())
            if self.cfg["predict"]["trickle_players"] > 0:
                self.spawn(self._trickle_loop())
        else:
            self.note("No Clash Royale API token: using the offline card list and no predictions.", "warn")
        if self.cfg["scrcpy"]["enabled"] and self.cfg["scrcpy"]["autostart"]:
            self.scrcpy.start()

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        self.scrcpy.stop()
        await self.api.close()
        self.db.close()

    def spawn(self, coro) -> None:
        """Run a coroutine on the server loop; callable from any thread."""
        if self.loop is None:
            coro.close()
            return

        def _go():
            task = self.loop.create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._task_done)

        if self._on_loop_thread():
            _go()
        else:
            self.loop.call_soon_threadsafe(_go)

    def _on_loop_thread(self) -> bool:
        try:
            return asyncio.get_running_loop() is self.loop
        except RuntimeError:
            return False

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("background task failed", exc_info=task.exception())
            self.note(f"Background error: {task.exception()}", "error")

    def touch(self) -> None:
        self.version += 1

    def note(self, text: str, level: str = "info") -> None:
        log.log(logging.WARNING if level != "info" else logging.INFO, text)
        self.messages = [*self.messages[-19:], {"t": time.time(), "level": level, "text": text}]
        self.touch()

    # ---- persistence ---------------------------------------------------
    def _load_state(self) -> dict:
        p = self.var / "state.json"
        try:
            return json.loads(p.read_text()) if p.is_file() else {}
        except ValueError:
            return {}

    def _save_state(self) -> None:
        (self.var / "state.json").write_text(json.dumps(
            {"my_tag": self.my_tag, "my_deck": self.my_deck, "my_trophies": self.my_trophies,
             "coach": self.coach}, indent=1))

    # ---- commands ------------------------------------------------------
    def dispatch(self, cmd: dict) -> dict:
        name = cmd.get("cmd")
        handler = getattr(self, f"_cmd_{name}", None)
        if handler is None:
            raise CommandError(f"unknown command {name!r}")
        with self.lock:
            try:
                result = handler(cmd) or {}
            except ValueError as e:
                raise CommandError(str(e)) from e
            self.touch()
            return result

    def _require_match(self) -> Match:
        if not self.match or not self.match.started:
            raise CommandError("no match running (press Start)")
        return self.match

    def _cmd_start(self, cmd: dict) -> dict:
        if len(self.my_deck) != 8:
            raise CommandError("set your 8-card deck first")
        if self.match and self.match.started and not self.match.ended:
            raise CommandError("a match is already running; end it first")
        self.match = Match(list(self.my_deck), self.rules, self.catalog)
        self.match.add("start", t=cmd.get("t"))
        self.match_id = self.db.save_match(self.match, self.my_tag)
        self.opp_history, self.opp_history_status = [], ""
        self.vision = {"status": "idle"}
        self._repredict()
        vcfg = self.cfg["vision"]
        if vcfg["enabled"] and vcfg["identify_on_start"]:
            self.spawn(self.identify_opponent(delay=vcfg["delay_seconds"]))
        return {"match_id": self.match_id}

    def _cmd_end(self, cmd: dict) -> dict:
        m = self._require_match()
        if not m.ended:
            m.add("end", t=cmd.get("t"))
            self.db.save_match(m, self.my_tag, self.match_id)
            if self.api.enabled and self.my_tag:
                self.spawn(self.reconcile(self.match_id, m))
        return {}

    def _cmd_play(self, cmd: dict) -> dict:
        m = self._require_match()
        side = cmd.get("side")
        if side not in SIDES:
            raise CommandError("side must be 'me' or 'opp'")
        card = cmd.get("card")
        if card is None and "slot" in cmd:
            card = self._card_for_slot(side, cmd["slot"], cmd.get("source", "seen"))
        if card not in self.catalog:
            raise CommandError(f"unknown card {card!r}")
        if side == "me" and card not in m.my_deck:
            raise CommandError(f"{card} isn't in your deck")
        if side == "opp" and card not in m.opp_revealed() and len(m.opp_revealed()) >= 8:
            raise CommandError("opponent already has 8 known cards")
        m.add("play", side, card, t=cmd.get("t"))
        self._after_change(side)
        return {"card": card}

    def _card_for_slot(self, side: str, slot: int, source: str) -> str:
        m = self.match
        if side == "me":
            cards = m.my_deck
        elif source == "predicted":
            cards = [c for c, _ in self.prediction.cards]
        elif source == "coach":
            cards = self.coach_slots()
        else:
            cards = m.opp_cards()
        if not 0 <= slot < len(cards):
            raise CommandError(f"no card in {side} {source} slot {slot + 1}")
        return cards[slot]

    def coach_slots(self) -> list[str]:
        """The opponent's 8 slots in coach mode: seen cards first, then the top predictions."""
        seen = self.match.opp_cards() if self.match else []
        predicted = [c for c, _ in self.prediction.cards if c not in seen]
        return (seen + predicted)[:8]

    def _cmd_set_coach(self, cmd: dict) -> dict:
        self.coach = bool(cmd.get("on"))
        self._save_state()
        return {"coach": self.coach}

    def _coach_active(self) -> bool:
        # Plain keys are global, so they only count while they can only mean a coach-mode play.
        return self.coach and bool(self.match) and self.match.started and not self.match.ended

    def _cmd_coach_key(self, cmd: dict) -> dict:
        if not self._coach_active():
            return {"ignored": True}
        return self._cmd_play({"side": "opp", "slot": cmd["slot"], "source": "coach", "t": cmd.get("t")})

    def _cmd_coach_undo(self, cmd: dict) -> dict:
        if not self._coach_active():
            return {"ignored": True}
        return self._cmd_undo(cmd)

    def _cmd_coach_hand(self, cmd: dict) -> dict:
        """Q/W/E/R: play a card from the opponent's hand, once all 4 are known."""
        if not self._coach_active():
            return {"ignored": True}
        m = self.match
        view = cycle.infer(m.cycle_deck("opp"), m.plays("opp"))
        if view.hand_unknown or len(view.hand) != cycle.HAND_SIZE:
            raise CommandError("their hand isn't fully known yet")
        return self._cmd_play({"side": "opp", "card": view.hand[cmd["slot"]], "t": cmd.get("t")})

    def _cmd_adjust(self, cmd: dict) -> dict:
        m = self._require_match()
        m.add("adjust", cmd["side"], amount=float(cmd.get("amount", 1)), t=cmd.get("t"))
        return {}

    def _cmd_ability(self, cmd: dict) -> dict:
        m = self._require_match()
        if not m.champion(cmd["side"]) and cmd.get("amount") is None:
            raise CommandError("no champion known for that side")
        m.add("ability", cmd["side"], amount=cmd.get("amount"), t=cmd.get("t"))
        return {}

    def _cmd_collector_destroyed(self, cmd: dict) -> dict:
        self._require_match().add("collector_destroyed", cmd["side"], t=cmd.get("t"))
        return {}

    def _cmd_next_phase(self, cmd: dict) -> dict:
        self._require_match().add("next_phase", t=cmd.get("t"))
        return {}

    def _cmd_undo(self, cmd: dict) -> dict:
        m = self._require_match()
        ev = m.undo()
        if ev and ev.type == "end":
            self.db.save_match(m, self.my_tag, self.match_id)
        if ev and ev.side == "opp":
            self._after_change("opp")
        return {"undone": ev.to_dict() if ev else None}

    def _cmd_reset(self, cmd: dict) -> dict:
        self.match, self.match_id = None, None
        self.opp_history, self.opp_history_status = [], ""
        self.vision = {"status": "idle"}
        self._repredict()
        return {}

    def _cmd_set_deck(self, cmd: dict) -> dict:
        cards = cmd.get("cards") or []
        bad = [c for c in cards if c not in self.catalog]
        if len(cards) != 8 or len(set(cards)) != 8 or bad:
            raise CommandError(f"need 8 distinct known cards (unknown: {bad})")
        self.my_deck = list(cards)
        self._save_state()
        return {}

    def _cmd_set_my_tag(self, cmd: dict) -> dict:
        self.my_tag = normalize_tag(cmd["tag"])
        self._save_state()
        self.spawn(self.refresh_my_deck())
        return {}

    def _cmd_refresh_deck(self, cmd: dict) -> dict:
        self.spawn(self.refresh_my_deck())
        return {}

    def _cmd_set_opp_tag(self, cmd: dict) -> dict:
        tag = (cmd.get("tag") or "").strip()
        if self.match:
            self.match.opp_tag = normalize_tag(tag) if tag else None
            self.match.opp_name = cmd.get("name") or self.match.opp_name
        if tag:
            self.spawn(self.load_opp_history(normalize_tag(tag)))
        else:
            self.opp_history, self.opp_history_status = [], ""
            self._fit_opp_deck()
            self._repredict()
        return {}

    def _cmd_set_opp_deck(self, cmd: dict) -> dict:
        """You picked or built the opponent's deck."""
        m = self._require_match()
        cards = cmd.get("cards") or []
        bad = [c for c in cards if c not in self.catalog]
        if len(cards) != 8 or len(set(cards)) != 8 or bad:
            raise CommandError(f"need 8 distinct known cards (unknown: {bad})")
        missing = [c for c in m.opp_revealed() if c not in cards]
        if missing:
            raise CommandError(f"they've already played {', '.join(missing)}, which isn't in that deck")
        m.opp_deck, m.opp_deck_source = list(cards), "manual"
        self._repredict()
        return {}

    def _cmd_clear_opp_deck(self, cmd: dict) -> dict:
        """Not their deck: go back to predictions until you pick or build one."""
        m = self._require_match()
        m.opp_deck, m.opp_deck_source = None, "dismissed"
        self._repredict()
        return {}

    def _fit_opp_deck(self) -> None:
        """Assume the opponent plays their most recent deck that has every card they've shown."""
        m = self.match
        if not m:
            return
        seen = set(m.opp_revealed())
        if m.opp_deck_source == "manual":
            if seen <= set(m.opp_deck):
                return
            self.note("They played a card that isn't in the deck you set, so it's no longer assumed.", "warn")
            m.opp_deck_source = None
        if m.opp_deck_source == "dismissed":
            return
        fit = next((d for d in self.opp_history if seen <= set(d)), None)  # history is newest first
        if m.opp_deck and fit != m.opp_deck:
            self.note("Switched to another of their recent decks." if fit else
                      "None of their recent decks fits what they've played: back to predictions.", "warn")
        m.opp_deck, m.opp_deck_source = (list(fit), "history") if fit else (None, None)

    def _cmd_identify(self, cmd: dict) -> dict:
        self.spawn(self.identify_opponent(delay=0))
        return {}

    def _cmd_search(self, cmd: dict) -> dict:
        """Open the focused search popup (for an opponent card we haven't seen yet)."""
        port = self.cfg["server"]["port"]
        subprocess.Popen([sys.executable, "-m", "clasharness.popup", "--port", str(port)])
        return {}

    def _cmd_open_search_url(self, cmd: dict) -> dict:
        url = self.vision.get("search_url")
        if url:
            webbrowser.open(url)
        return {}

    def _cmd_scrcpy(self, cmd: dict) -> dict:
        (self.scrcpy.start if cmd.get("action", "start") == "start" else self.scrcpy.stop)()
        return {}

    def _cmd_crawl(self, cmd: dict) -> dict:
        if self.crawl_status.get("running"):
            raise CommandError("a crawl is already running")
        self.spawn(self.run_crawl(int(cmd.get("players", 200)), bool(cmd.get("pol_top", False))))
        return {}

    def _after_change(self, side: str) -> None:
        if side == "opp":
            self._fit_opp_deck()
            self._repredict()

    def _repredict(self) -> None:
        seen = self.match.opp_cards() if self.match else []
        p = self.cfg["predict"]
        self.prediction = predict(self.meta, seen, self.opp_history or None,
                                  min_support=p["min_support"], history_weight=p["history_weight"])

    # ---- snapshot ------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            match = self.match.snapshot() if self.match else {"status": "idle", "my_deck": self.my_deck}
            return {
                "version": self.version,
                "match": match,
                "match_id": self.match_id,
                "my_tag": self.my_tag,
                "my_deck": self.my_deck,
                "my_trophies": self.my_trophies,
                "opponent": {
                    "tag": self.match.opp_tag if self.match else None,
                    "name": self.match.opp_name if self.match else None,
                    "history_decks": self.opp_history[:5],
                    "history_status": self.opp_history_status,
                },
                "prediction": self.prediction.to_dict(),
                "vision": self.vision,
                "vision_crop": self.cfg["vision"]["crop"],
                "crawl": self.crawl_status,
                "dataset": {"decks_loaded": len(self.meta), **self._db_stats()},
                "api": {"enabled": self.api.enabled},
                "scrcpy": self.scrcpy.status(),
                "messages": self.messages[-8:],
                "hotkeys": self.cfg["hotkeys"],
                "coach": self.coach,
                "coach_slots": self.coach_slots(),
            }

    def _db_stats(self) -> dict:
        at, stats = self._stats_cache
        if time.time() - at > 5:  # COUNT(*) over big tables isn't free; the UI polls at 10 Hz
            stats = self.db.stats()
            self._stats_cache = (time.time(), stats)
        return stats

    # ---- async work ----------------------------------------------------
    def reload_meta(self) -> None:
        days = self.cfg["predict"]["max_deck_age_days"]
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%dT%H%M%S.000Z")
        index = DeckIndex.from_rows(self.db.load_decks(since))
        with self.lock:
            self.meta = index
            self._repredict()
            self.touch()

    async def refresh_cards(self) -> None:
        try:
            payload = await self.api.cards()
        except ApiError as e:
            self.note(f"Card list fetch failed ({e}); using cached/offline list.", "warn")
            return
        (self.var / "cards.json").write_text(json.dumps(payload))
        with self.lock:
            self.catalog = Catalog.from_api_payload(payload)
            if self.match:
                self.match.catalog = self.catalog
            self.touch()

    async def refresh_my_deck(self) -> None:
        if not self.my_tag:
            return
        try:
            player = await self.api.player(self.my_tag)
        except ApiError as e:
            self.note(f"Couldn't load your profile: {e}", "error")
            return
        deck = deck_from_player(player)
        with self.lock:
            if len(deck) == 8:
                self.my_deck = deck
            self.my_trophies = player.get("trophies")
            self.db.upsert_player({"tag": player["tag"], "name": player["name"], "trophies": self.my_trophies,
                                   "clan_tag": (player.get("clan") or {}).get("tag"),
                                   "clan_name": (player.get("clan") or {}).get("name")})
            self._save_state()
        self.note(f"Loaded deck for {player['name']}.")

    async def load_opp_history(self, tag: str) -> None:
        self.opp_history_status = "loading"
        self.touch()
        try:
            battles = await ingest_battlelog(self.api, self.db, tag)
        except ApiError as e:
            self.opp_history_status = f"failed: {e}"
            self.touch()
            return
        decks: list[list[str]] = []
        name = None
        for b in battles:
            for side in b["sides"]:
                if side["tag"] == tag and len(side["cards"]) == 8:
                    name = name or side["name"]
                    if side["cards"] not in decks:
                        decks.append(side["cards"])
        with self.lock:
            if self.match and self.match.opp_tag in (None, tag):
                self.match.opp_tag = tag
                self.match.opp_name = self.match.opp_name or name
            self.opp_history = decks
            self.opp_history_status = f"{len(decks)} recent decks"
            self._fit_opp_deck()
            self._repredict()
            self.touch()

    async def identify_opponent(self, delay: float = 0) -> None:
        from .vision.capture import CaptureError, crop, screenshot
        from .vision.reader import BannerReader, ReadError
        from .vision.resolve import resolve

        vcfg = self.cfg["vision"]
        match = self.match
        if delay:
            await asyncio.sleep(delay)
        deadline = time.time() + vcfg["retry_for_seconds"]
        while True:
            self.vision = {"status": "capturing"}
            self.touch()
            try:
                png = await screenshot(vcfg["adb_path"])
            except CaptureError as e:
                self.vision = {"status": "error", "error": f"screenshot: {e}"}
                self.touch()
                return
            vdir = self.var / "vision"
            vdir.mkdir(exist_ok=True)
            (vdir / "last_screen.png").write_bytes(png)
            banner = crop(png, vcfg["crop"])
            (vdir / "last_crop.png").write_bytes(banner)

            self.vision = {"status": "reading"}
            self.touch()
            try:
                orc = self.cfg["openrouter"]
                reader = BannerReader(orc["model"], self.cfg.openrouter_key, orc["base_url"])
                read = await reader.read(banner)
            except ReadError as e:
                self.vision = {"status": "error", "error": str(e)}
                self.touch()
                return
            if read.player_name.strip():
                break
            # No name on screen yet: still matchmaking or fading into the battle.
            if self.match is not match or time.time() + vcfg["retry_seconds"] > deadline:
                self.vision = {"status": "not_found", "read": read.model_dump(),
                               "error": "No opponent name on screen. Press Read screen once the battle shows."}
                self.touch()
                return
            self.vision = {"status": "waiting", "read": read.model_dump()}
            self.touch()
            await asyncio.sleep(vcfg["retry_seconds"])

        self.vision = {"status": "resolving", "read": read.model_dump()}
        if self.match:
            self.match.opp_name = read.player_name
        self.touch()
        res = await resolve(self.api, self.db, read.player_name, read.clan_name, self.my_trophies, vcfg["search_url"])
        self.vision = {"status": res.status, "read": read.model_dump(), **res.to_dict()}
        self.touch()
        if res.tag:
            with self.lock:
                if self.match:
                    self.match.opp_tag = res.tag
            await self.load_opp_history(res.tag)

    async def reconcile(self, match_id: int, match: Match, attempts: int = 3) -> None:
        """Find the finished match in my battle log; store the opponent's full deck and the result."""
        delay = self.cfg["predict"]["reconcile_delay_seconds"]
        for _ in range(attempts):
            await asyncio.sleep(delay)
            try:
                log_ = await self.api.battlelog(self.my_tag)
            except ApiError as e:
                self.note(f"Battle log lookup failed: {e}", "warn")
                continue
            battle = self._find_battle(log_, match)
            if battle:
                break
        else:
            self.note("Couldn't find this match in your battle log.", "warn")
            return
        me, opp = battle["sides"]
        result = "win" if me["crowns"] > opp["crowns"] else "loss" if me["crowns"] < opp["crowns"] else "draw"
        self.db.reconcile_match(match_id, opp_tag=opp["tag"], opp_name=opp["name"], opp_deck=opp["cards"],
                                result=result, crowns=[me["crowns"], opp["crowns"]], battle_time=battle["time"])
        if battle["ranked"]:
            self.db.ingest_battle(battle)
        self.db.upsert_player(opp, battle["time"])
        with self.lock:
            if self.match is match:
                match.opp_tag, match.opp_name = opp["tag"], opp["name"]
        self.note(f"Saved match vs {opp['name']} ({result}, {me['crowns']}-{opp['crowns']}).")

    @staticmethod
    def _find_battle(raw_log: list[dict], match: Match) -> dict | None:
        revealed = set(match.opp_revealed())
        best, best_score = None, None
        for b in raw_log:
            parsed = parse_battle(b)
            if not parsed or not parsed["time"]:
                continue
            gap = abs(parse_battle_time(parsed["time"]) - (match.end_t or match.start_t))
            if gap > 15 * 60:
                continue
            # Prefer battles whose opponent deck contains what we saw, then the closest in time.
            score = (len(revealed - set(parsed["sides"][1]["cards"])), gap)
            if best_score is None or score < best_score:
                best, best_score = parsed, score
        return best

    async def run_crawl(self, players: int, pol_top: bool = False) -> None:
        self.crawl_status = {"running": True, "players": 0, "battles": 0, "errors": 0, "queued": 0}
        self.touch()

        def progress(stats: dict) -> None:
            self.crawl_status = {"running": True, **stats}
            self.touch()

        p = self.cfg["predict"]
        try:
            seeds = await seed_tags(self.api, self.db, self.my_tag, self.my_trophies, p["trophy_window"], pol_top)
            stats = await crawl(self.api, self.db, seeds, players, self.my_trophies, p["trophy_window"], progress)
        finally:
            self.crawl_status = {**self.crawl_status, "running": False, "finished_at": time.time()}
        await asyncio.to_thread(self.reload_meta)
        self.note(f"Crawl done: {stats['players']} players, {stats['battles']} battles.")

    async def _trickle_loop(self) -> None:
        p = self.cfg["predict"]
        while True:
            await asyncio.sleep(p["trickle_interval_seconds"])
            if self.crawl_status.get("running") or (self.match and self.match.started and not self.match.ended):
                continue  # don't compete with a live match for rate limit
            await self.run_crawl(p["trickle_players"])
