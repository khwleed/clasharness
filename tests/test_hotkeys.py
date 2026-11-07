import pytest

from clasharness.config import DEFAULTS, HOTKEYS, HOTKEYS_MAC
from clasharness.hotkeys import bindings, exact_hotkeys


def all_keys(hk: dict) -> list[str]:
    lists = hk["my_cards"] + hk["opp_seen"] + hk["opp_predicted"] + hk["coach_opp"] + hk["coach_hand"]
    return lists + [v for v in hk.values() if isinstance(v, str)]


@pytest.mark.parametrize("hk", [HOTKEYS, HOTKEYS_MAC], ids=["default", "mac"])
def test_bindings_cover_every_action_once(hk):
    b = bindings(hk)
    keys = all_keys(hk)
    # no two actions share a key
    assert len(keys) == len(set(keys)) == len(b)
    assert b[hk["my_cards"][0]] == {"cmd": "play", "side": "me", "slot": 0}
    assert b[hk["opp_seen"][7]] == {"cmd": "play", "side": "opp", "slot": 7, "source": "seen"}
    assert b[hk["opp_predicted"][0]]["source"] == "predicted"
    assert b[hk["undo"]] == {"cmd": "undo"}
    assert b["1"] == {"cmd": "coach_key", "slot": 0}
    assert b["8"] == {"cmd": "coach_key", "slot": 7}
    assert b["q"] == {"cmd": "coach_hand", "slot": 0}
    assert b["r"] == {"cmd": "coach_hand", "slot": 3}
    assert b["<backspace>"] == {"cmd": "coach_undo"}


def test_mac_bindings_avoid_option_and_function_keys():
    # Option+letter types a symbol on macOS, and F-keys need Fn.
    for key in all_keys(HOTKEYS_MAC):
        assert "<alt>" not in key and "<f" not in key, key


def test_defaults_pick_a_set():
    assert DEFAULTS["hotkeys"]["start"] in (HOTKEYS["start"], HOTKEYS_MAC["start"])


def press(listener, *keys):
    for k in keys:
        listener._on_press(k, False)
    for k in reversed(keys):
        listener._on_release(k, False)


def test_plain_keys_dont_fire_under_a_modifier():
    # pynput raises ImportError (not ModuleNotFoundError) when there is no display.
    keyboard = pytest.importorskip("pynput.keyboard", exc_type=ImportError)
    fired = []
    listener = exact_hotkeys({k: (lambda k=k: fired.append(k)) for k in ["1", "<ctrl>+1", "<ctrl>+="]})
    one, eq = keyboard.KeyCode.from_char("1"), keyboard.KeyCode.from_char("=")
    press(listener, keyboard.Key.ctrl_l, one)  # Ctrl+1: your card, not coach slot 1
    press(listener, one)
    press(listener, keyboard.Key.ctrl, keyboard.Key.shift, eq)  # Ctrl+Shift+= isn't Ctrl+=
    press(listener, keyboard.Key.ctrl, eq)
    assert fired == ["<ctrl>+1", "1", "<ctrl>+="]
