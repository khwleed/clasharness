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
    for i, key in enumerate(hk.get("coach_opp", [])):
        out[key] = {"cmd": "coach_key", "slot": i}
    for i, key in enumerate(hk.get("coach_hand", [])):
        out[key] = {"cmd": "coach_hand", "slot": i}
    simple = {
        "opp_search": {"cmd": "search"},
        "start": {"cmd": "start"},
        "end": {"cmd": "end"},
        "next_phase": {"cmd": "next_phase"},
        "undo": {"cmd": "undo"},
        "coach_undo": {"cmd": "coach_undo"},
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


def exact_hotkeys(hotkeys: dict[str, Callable[[], None]]):
    """A pynput GlobalHotKeys that fires only when exactly the bound modifiers are held.

    pynput ignores extra keys, so on its own Ctrl+1 would also fire a plain "1" binding
    (coach mode), and Ctrl+Shift+= would fire Ctrl+=.
    """
    from pynput import keyboard

    mods = {keyboard.Key.ctrl, keyboard.Key.alt, keyboard.Key.shift, keyboard.Key.cmd}

    class ExactHotKeys(keyboard.GlobalHotKeys):
        def __init__(self):
            self.held: set = set()
            gated = {}
            for combo, fn in hotkeys.items():
                need = {k for k in keyboard.HotKey.parse(combo) if k in mods}
                gated[combo] = lambda fn=fn, need=need: fn() if self.held == need else None
            super().__init__(gated)

        def _on_press(self, key, injected):
            if self.canonical(key) in mods:
                self.held.add(self.canonical(key))
            super()._on_press(key, injected)

        def _on_release(self, key, injected):
            self.held.discard(self.canonical(key))
            super()._on_release(key, injected)

    return ExactHotKeys()


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
        listener = exact_hotkeys({key: make(cmd) for key, cmd in bindings(hk).items()})
        listener.daemon = True
        listener.start()
    except Exception as e:
        on_error(f"Global hotkeys failed to start: {e}")
        return None
    return listener
