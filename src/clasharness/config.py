"""Configuration: built-in defaults overlaid with the user's config.toml."""

from __future__ import annotations

import copy
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

# Coach mode: plain keys 1-8 log the opponent's 8 card slots (seen cards first, then
# the top predictions). They only fire while coach mode is on and a match is running.
COACH_KEYS = [str(i) for i in range(1, 9)]
# Coach mode: once the opponent's whole hand is known, Q/W/E/R log its cards in the order shown.
COACH_HAND_KEYS = ["q", "w", "e", "r"]
# Coach mode: plain Backspace undoes the last thing logged.
COACH_UNDO_KEY = "<backspace>"

HOTKEYS: dict[str, Any] = {
    "my_cards": ["<f1>", "<f2>", "<f3>", "<f4>", "<f5>", "<f6>", "<f7>", "<f8>"],
    "opp_seen": [f"<alt>+{i}" for i in range(1, 9)],
    "opp_predicted": ["<alt>+q", "<alt>+w", "<alt>+e", "<alt>+r"],
    "coach_opp": COACH_KEYS,
    "coach_hand": COACH_HAND_KEYS,
    "coach_undo": COACH_UNDO_KEY,
    "opp_search": "<alt>+<space>",
    "start": "<f9>",
    "end": "<f10>",
    "next_phase": "<f11>",
    "undo": "<f12>",
    "identify": "<alt>+i",
    "my_ability": "<ctrl>+a",
    "opp_ability": "<alt>+a",
    "my_plus": "<ctrl>+=",
    "my_minus": "<ctrl>+-",
    "opp_plus": "<alt>+=",
    "opp_minus": "<alt>+-",
}

# macOS: Option+letter types a symbol (Option+Q is "œ"), so <alt> bindings never match,
# and F-keys need Fn or trigger system features. Ctrl+letter/digit arrive intact.
# Rows mirror the board: digits = your cards, the row below = opponent seen, below that = predicted.
HOTKEYS_MAC: dict[str, Any] = {
    "my_cards": [f"<ctrl>+{i}" for i in range(1, 9)],
    "opp_seen": [f"<ctrl>+{c}" for c in "qwertyui"],
    "opp_predicted": [f"<ctrl>+{c}" for c in "asdf"],
    "coach_opp": COACH_KEYS,
    "coach_hand": COACH_HAND_KEYS,
    "coach_undo": COACH_UNDO_KEY,
    "opp_search": "<ctrl>+k",
    "start": "<ctrl>+g",
    "end": "<ctrl>+x",
    "next_phase": "<ctrl>+n",
    "undo": "<ctrl>+z",
    "identify": "<ctrl>+o",
    "my_ability": "<ctrl>+j",
    "opp_ability": "<ctrl>+l",
    "my_plus": "<ctrl>+=",
    "my_minus": "<ctrl>+-",
    "opp_plus": "<ctrl>+]",
    "opp_minus": "<ctrl>+[",
}

DEFAULTS: dict[str, Any] = {
    "api": {
        "token": "",
        # RoyaleAPI proxy: whitelist its IP on your developer key instead of your own.
        "base_url": "https://proxy.royaleapi.dev/v1",
        "my_tag": "",
        "requests_per_second": 5.0,
    },
    "openrouter": {
        "api_key": "",  # falls back to OPENROUTER_API_KEY
        "base_url": "https://openrouter.ai/api/v1",
        # Must accept image input; the deepseek-flash-latest alias is text-only.
        "model": "deepseek/deepseek-v4.1-flash",
    },
    "server": {"host": "127.0.0.1", "port": 8765, "open_browser": True},
    "storage": {"dir": "var"},
    "scrcpy": {
        "enabled": True,
        "autostart": False,
        "path": "scrcpy",
        "args": ["--stay-awake", "--window-title=Clash Royale", "--max-size=1600"],
    },
    # Show the phone inside the app (needs scrcpy installed: it uses scrcpy's phone-side server).
    "phone": {
        "embed": True,
        "serial": "",           # adb serial, only needed with several phones/emulators connected
        "max_size": 1600,       # longest side of the video, in pixels
        "bit_rate": 8_000_000,
        "max_fps": 60,
        "keyframe_interval": 1,  # seconds
        "stay_awake": True,
        "server_path": "",      # scrcpy-server file; found next to scrcpy when empty
        "server_version": "",   # its version; read from `scrcpy --version` when empty
        # Keys that tap the phone screen while the app has focus: [{key, x, y, name}], x/y as
        # fractions of the screen. Usually set up in the UI (Tap keys under the phone).
        "taps": [],
    },
    "elixir": {
        "start": 5.0,
        "max": 10.0,
        "seconds_per_elixir": 2.8,
        # Ladder / Path of Legends, per Supercell support: x1 for 2 minutes, x2 for the
        # third minute, overtime x2 then x3. Times are seconds of game clock.
        "phases": [
            {"start": 0, "multiplier": 1, "name": "Single elixir"},
            {"start": 120, "multiplier": 2, "name": "Double elixir"},
            {"start": 180, "multiplier": 2, "name": "Overtime"},
            {"start": 240, "multiplier": 3, "name": "Overtime x3"},
        ],
        "regulation_seconds": 180,
        "end": 300,
    },
    "vision": {
        "enabled": True,
        "adb_path": "adb",
        "identify_on_start": True,
        "delay_seconds": 1.5,
        # If the name banner isn't on screen yet (Start pressed during matchmaking or the
        # loading fade), screenshot again every retry_seconds for up to retry_for_seconds.
        "retry_seconds": 2.0,
        "retry_for_seconds": 20.0,
        # Opponent name + clan banner, as fractions of the screenshot (x0, y0, x1, y1).
        "crop": [0.0, 0.0, 0.75, 0.085],
        # Opened in your browser when the name can't be matched automatically.
        "search_url": "https://royaleapi.com/player/search/results?q={q}",
    },
    "predict": {
        "min_support": 15,
        "max_deck_age_days": 21,
        "history_weight": 0.8,
        "trophy_window": 800,
        # Background top-up of the deck dataset / player index while the app runs.
        "trickle_players": 15,
        "trickle_interval_seconds": 600,
        # After "end match", wait this long before looking the battle up in your battle log.
        "reconcile_delay_seconds": 20,
    },
    "hotkeys": {"enabled": True, **(HOTKEYS_MAC if sys.platform == "darwin" else HOTKEYS)},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    path: Path | None = None

    def __getitem__(self, section: str) -> dict[str, Any]:
        return self.raw[section]

    @property
    def storage_dir(self) -> Path:
        d = Path(self.raw["storage"]["dir"]).expanduser()  # relative paths are relative to where you run it
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def openrouter_key(self) -> str | None:
        return self.raw["openrouter"]["api_key"] or os.environ.get("OPENROUTER_API_KEY") or None


def load_config(path: str | os.PathLike | None = None) -> Config:
    candidates = [Path(path)] if path else [Path.cwd() / "config.toml"]
    for p in candidates:
        if p.is_file():
            with p.open("rb") as f:
                return Config(_merge(DEFAULTS, tomllib.load(f)), p)
    return Config()
