"""Command line entry point.

    python -m clasharness              # run the harness (web UI + hotkeys + scrcpy)
    python -m clasharness crawl -n 500 # grow the deck dataset / player index
    python -m clasharness check        # verify the API token and your tag
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import webbrowser

from .api import ApiError, ClashApi, deck_from_player
from .config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="clasharness")
    ap.add_argument("--config", help="path to config.toml (default: ./config.toml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("run", help="start the harness (default)")
    c = sub.add_parser("crawl", help="fetch battle logs to build the prediction dataset")
    c.add_argument("-n", "--players", type=int, default=300)
    c.add_argument("--pol-top", action="store_true", help="also seed from Path of Legends top players")
    sub.add_parser("check", help="check API access")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    {"crawl": lambda: asyncio.run(_crawl(cfg, args.players, args.pol_top)),
     "check": lambda: asyncio.run(_check(cfg))}.get(args.command, lambda: _run(cfg))()


def _run(cfg) -> None:
    import uvicorn

    from .harness import Harness
    from .hotkeys import start_listener
    from .server import create_app

    harness = Harness(cfg)
    host, port = cfg["server"]["host"], cfg["server"]["port"]

    def started():
        if cfg["hotkeys"]["enabled"]:
            start_listener(cfg["hotkeys"], harness.dispatch, lambda msg: harness.note(msg, "warn"))
        if cfg["server"]["open_browser"]:
            webbrowser.open(f"http://{host}:{port}/")

    uvicorn.run(create_app(harness, started), host=host, port=port, log_level="warning")


async def _crawl(cfg, players: int, pol_top: bool) -> None:
    from .harness import Harness

    h = Harness(cfg)
    if not h.api.enabled:
        raise SystemExit("Set [api].token in config.toml first.")
    if h.my_tag:
        await h.refresh_my_deck()
    h.loop = asyncio.get_running_loop()
    print(f"Crawling up to {players} players around {h.my_trophies or 'any'} trophies…")
    await h.run_crawl(players, pol_top)
    print(h.crawl_status, h.db.stats())
    await h.api.close()


async def _check(cfg) -> None:
    api = ClashApi(cfg["api"]["token"], cfg["api"]["base_url"])
    try:
        cards = await api.cards()
        print(f"API OK: {len(cards.get('items', []))} cards.")
        if cfg["api"]["my_tag"]:
            p = await api.player(cfg["api"]["my_tag"])
            print(f"{p['name']} ({p['tag']}), {p.get('trophies')} trophies, deck: {', '.join(deck_from_player(p))}")
    except ApiError as e:
        hint = " (is the RoyaleAPI proxy IP whitelisted on your key?)" if e.status == 403 else ""
        raise SystemExit(f"API error {e}{hint}") from e
    finally:
        await api.close()


if __name__ == "__main__":
    main()
