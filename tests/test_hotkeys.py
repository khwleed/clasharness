from clasharness.config import DEFAULTS
from clasharness.hotkeys import bindings


def test_default_bindings_cover_every_action():
    b = bindings(DEFAULTS["hotkeys"])
    assert b["<f1>"] == {"cmd": "play", "side": "me", "slot": 0}
    assert b["<alt>+8"] == {"cmd": "play", "side": "opp", "slot": 7, "source": "seen"}
    assert b["<alt>+q"]["source"] == "predicted"
    assert b["<f12>"] == {"cmd": "undo"}
    # no two actions share a key
    hk = DEFAULTS["hotkeys"]
    keys = hk["my_cards"] + hk["opp_seen"] + hk["opp_predicted"] + [v for v in hk.values() if isinstance(v, str)]
    assert len(keys) == len(set(keys)) == len(b)
