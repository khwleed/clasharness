"""System-wide hotkeys, so you can log plays while scrcpy has keyboard focus."""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)


def bindings(hk: dict) -> dict[str, dict]:
    """Hotkey string -> command, from the [hotkeys] config section."""
    out: dict[str, dict] = {}
    for i, key in enumerate(hk["my_cards"]):
        out[key] = {"cmd": "play", "side": "me", "slot": i}
    for i, key in enumerate(hk["opp_seen"]):
        out[key] = {"cmd": "play", "side": "opp", "slot": i, "source": "seen"}
    for i, key in enumerate(hk["opp_predicted"]):
        out[key] = {"cmd": "play", "side": "opp", "slot": i, "source": "predicted"}
    simple = {
        "opp_search": {"cmd": "search"},
        "start": {"cmd": "start"},
        "end": {"cmd": "end"},
        "next_phase": {"cmd": "next_phase"},
        "undo": {"cmd": "undo"},
        "identify": {"cmd": "identify"},
        "my_ability": {"cmd": "ability", "side": "me"},
        "opp_ability": {"cmd": "ability", "side": "opp"},
        "my_plus": {"cmd": "adjust", "side": "me", "amount": 1},
        "my_minus": {"cmd": "adjust", "side": "me", "amount": -1},
        "opp_plus": {"cmd": "adjust", "side": "opp", "amount": 1},
        "opp_minus": {"cmd": "adjust", "side": "opp", "amount": -1},
    }
    for name, cmd in simple.items():
        if hk.get(name):
            out[hk[name]] = cmd
    return out


def start_listener(hk: dict, dispatch: Callable[[dict], object], on_error: Callable[[str], None]):
    """Start a pynput GlobalHotKeys listener thread. Returns it, or None if unavailable."""
    try:
        from pynput import keyboard
    except Exception as e:  # no X server, missing permissions, unsupported platform...
        on_error(f"Global hotkeys unavailable ({e}). Use the browser UI instead.")
        return None

    def make(cmd: dict):
        def fire():
            try:
                dispatch(dict(cmd))
            except Exception as e:  # never let a bad keypress kill the listener
                on_error(str(e))
        return fire

    try:
        listener = keyboard.GlobalHotKeys({key: make(cmd) for key, cmd in bindings(hk).items()})
        listener.daemon = True
        listener.start()
    except Exception as e:
        on_error(f"Global hotkeys failed to start: {e}")
        return None
    return listener
