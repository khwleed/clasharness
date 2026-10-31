"""clasharness as a desktop app: the harness UI and your phone's screen in one window.

    ./run.sh desktop

Runs the same server as `./run.sh`, but shows it in a native window (pywebview, which
uses the system WebKit on macOS) instead of a browser tab. The phone screen is streamed
into the window, so there is no separate scrcpy window. Closing the window quits.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

import uvicorn

from clasharness.config import load_config
from clasharness.harness import Harness
from clasharness.hotkeys import start_listener
from clasharness.server import create_app

TITLE = "Clash Harness"


def main() -> None:
    ap = argparse.ArgumentParser(prog="clasharness-desktop")
    ap.add_argument("--config", help="path to config.toml (default: ./config.toml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--debug", action="store_true", help="enable the web inspector (right-click → Inspect)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        import webview
    except ImportError:
        sys.exit("pywebview isn't installed. Run ./run.sh desktop (it installs it), "
                 "or: .venv/bin/pip install -e '.[desktop]'")

    cfg = load_config(args.config)
    harness = Harness(cfg)
    host, port = cfg["server"]["host"], cfg["server"]["port"]

    def started():
        if cfg["hotkeys"]["enabled"]:
            start_listener(cfg["hotkeys"], harness.dispatch, lambda msg: harness.note(msg, "warn"))

    # uvicorn runs on a background thread: the window needs the main thread on macOS.
    server = uvicorn.Server(uvicorn.Config(create_app(harness, started), host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            sys.exit(f"The server didn't start. Is something else (another clasharness?) using port {port}?")
        time.sleep(0.05)

    webview.create_window(TITLE, f"http://{host}:{port}/", width=1600, height=1000, min_size=(900, 600),
                          background_color="#0f1420")
    try:
        webview.start(debug=args.debug)
    finally:
        server.should_exit = True
        thread.join(10)


if __name__ == "__main__":
    main()
