"""Drive a whole match through the HTTP command endpoint (the same path hotkeys use)."""

import time

import pytest
from fastapi.testclient import TestClient

from clasharness.api import parse_battle_time
from clasharness.config import DEFAULTS, Config, _merge
from clasharness.harness import Harness
from clasharness.server import create_app

BATTLE_T = parse_battle_time("20261030T150000.000Z")


@pytest.fixture
def client(tmp_path, api):
    cfg = Config(_merge(DEFAULTS, {
        "storage": {"dir": str(tmp_path)},
        "api": {"my_tag": "#ME123"},
        "vision": {"enabled": False},
        "scrcpy": {"enabled": False},
        "phone": {"embed": False},
        "predict": {"trickle_players": 0, "reconcile_delay_seconds": 0, "min_support": 1},
    }))
    harness = Harness(cfg, api=api)
    with TestClient(create_app(harness)) as c:
        c.harness = harness
        yield c


def wait_for(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


def cmd(client, **body):
    r = client.post("/api/command", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_full_match(client):
    wait_for(lambda: len(client.get("/api/state").json()["my_deck"]) == 8)

    # Seed the prediction dataset from the fake battle log.
    cmd(client, cmd="crawl", players=3)
    wait_for(lambda: client.get("/api/state").json()["dataset"]["decks_loaded"] > 0)

    cmd(client, cmd="start", t=BATTLE_T - 180)
    cmd(client, cmd="play", side="me", slot=0, t=BATTLE_T - 175)  # F1: hog rider
    cmd(client, cmd="play", side="opp", card="giant", t=BATTLE_T - 170)
    s = client.get("/api/state").json()
    assert s["match"]["me"]["cycle"]["not_in_hand"] == ["hog-rider"]
    assert s["match"]["opp"]["revealed"] == ["giant"]
    pred = [c["card"] for c in s["prediction"]["cards"]]
    assert "witch" in pred  # the giant deck in the fake log

    cmd(client, cmd="play", side="opp", slot=0, source="predicted", t=BATTLE_T - 160)  # Alt+Q
    assert len(client.get("/api/state").json()["match"]["opp"]["revealed"]) == 2
    cmd(client, cmd="undo")
    assert client.get("/api/state").json()["match"]["opp"]["revealed"] == ["giant"]

    r = client.post("/api/command", json={"cmd": "play", "side": "me", "card": "golem"})
    assert r.status_code == 400  # not in my deck

    cmd(client, cmd="end", t=BATTLE_T)
    wait_for(lambda: (client.get("/api/matches").json() or [{}])[0].get("result") is not None)
    m = client.get("/api/matches").json()[0]
    assert m["result"] == "win" and m["opp_name"] == "Giant Guy"
    assert m["opp_deck"][0] == "giant"
    events = client.get(f"/api/matches/{m['id']}/events").json()
    assert [e["type"] for e in events] == ["start", "play", "play", "end"]


def test_start_requires_deck(tmp_path):
    cfg = Config(_merge(DEFAULTS, {"storage": {"dir": str(tmp_path)}, "vision": {"enabled": False}}))
    h = Harness(cfg)
    from clasharness.harness import CommandError
    with pytest.raises(CommandError):
        h.dispatch({"cmd": "start"})


def test_websocket_streams_state(client):
    with client.websocket_connect("/ws") as ws:
        assert "match" in ws.receive_json()


def test_coach_keys(client):
    wait_for(lambda: len(client.get("/api/state").json()["my_deck"]) == 8)
    cmd(client, cmd="crawl", players=3)
    wait_for(lambda: client.get("/api/state").json()["dataset"]["decks_loaded"] > 0)

    # Plain 1-8 are global keys, so they do nothing unless coach mode is on and a match runs.
    assert cmd(client, cmd="coach_key", slot=0)["ignored"]
    cmd(client, cmd="set_coach", on=True)
    assert client.get("/api/state").json()["coach"] is True
    assert cmd(client, cmd="coach_key", slot=0)["ignored"]

    cmd(client, cmd="start")
    cmd(client, cmd="play", side="opp", card="giant")
    s = client.get("/api/state").json()
    slots = s["coach_slots"]
    assert slots[0] == "giant"  # seen cards come first...
    assert slots[1:] == [c["card"] for c in s["prediction"]["cards"]][: len(slots) - 1]  # ...then predictions

    # Key 2 logs the top prediction; it becomes seen card 2, so it keeps its key.
    played = cmd(client, cmd="coach_key", slot=1)["card"]
    assert played == slots[1]
    s = client.get("/api/state").json()
    assert s["match"]["opp"]["revealed"] == ["giant", played]
    assert s["coach_slots"][1] == played

    cmd(client, cmd="set_coach", on=False)
    assert cmd(client, cmd="coach_key", slot=0)["ignored"]


def test_ui_files_are_revalidated(client):
    for path in ("/", "/static/app.js", "/static/style.css"):
        assert client.get(path).headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/api/state").headers


OPP_TAG = "#P9Q2YL"


def opponent_log(fake_server):
    """An opponent's battle log, newest first: a Golem deck, then a Giant deck.
    (Their tag uses real tag characters: the app reads a letter O as zero.)"""
    import copy
    giant, golem = copy.deepcopy(fake_server.battlelogs["#ME123"][:2])
    for b in (giant, golem):
        b["opponent"][0]["tag"] = OPP_TAG
    golem["battleTime"] = "20261030T160000.000Z"
    return [golem, giant]


def test_opponent_deck_is_assumed_from_their_battle_log(client, fake_server):
    fake_server.battlelogs[OPP_TAG] = opponent_log(fake_server)
    wait_for(lambda: len(client.get("/api/state").json()["my_deck"]) == 8)
    cmd(client, cmd="start")
    cmd(client, cmd="set_opp_tag", tag=OPP_TAG)
    wait_for(lambda: client.get("/api/state").json()["match"]["opp"]["deck_source"] == "history")
    s = client.get("/api/state").json()
    opp = s["match"]["opp"]
    assert "golem" in opp["cards"] and len(opp["cards"]) == 8  # their newest deck
    assert s["prediction"]["mode"] == "complete"
    assert opp["cycle"]["hand_unknown"] == 4  # all 8 known, none played yet

    # Giant isn't in the Golem deck, so switch to their older deck that has it.
    cmd(client, cmd="play", side="opp", card="giant")
    opp = client.get("/api/state").json()["match"]["opp"]
    assert opp["cards"][0] == "giant" and "witch" in opp["cards"] and opp["deck_source"] == "history"
    assert cmd(client, cmd="play", side="opp", slot=1, source="seen")["card"] == opp["cards"][1]

    # A card in none of their decks drops the assumption; undo brings it back.
    cmd(client, cmd="play", side="opp", card="hog-rider")
    assert client.get("/api/state").json()["match"]["opp"]["deck_source"] is None
    cmd(client, cmd="undo")
    assert client.get("/api/state").json()["match"]["opp"]["deck_source"] == "history"

    # "Not their deck" sticks until you pick one.
    cmd(client, cmd="clear_opp_deck")
    cmd(client, cmd="play", side="opp", card="witch")
    s = client.get("/api/state").json()
    assert s["match"]["opp"]["deck_source"] is None and s["match"]["opp"]["cards"] == s["match"]["opp"]["revealed"]


def test_build_opponent_deck(client):
    wait_for(lambda: len(client.get("/api/state").json()["my_deck"]) == 8)
    cmd(client, cmd="start")
    cmd(client, cmd="play", side="opp", card="giant")
    deck = ["giant", "witch", "zap", "fireball", "mega-minion", "minions", "arrows", "prince"]
    r = client.post("/api/command", json={"cmd": "set_opp_deck", "cards": deck[1:] + ["golem"]})
    assert r.status_code == 400 and "giant" in r.json()["detail"]  # must contain what they've played
    cmd(client, cmd="set_opp_deck", cards=deck)
    opp = client.get("/api/state").json()["match"]["opp"]
    assert opp["deck_source"] == "manual" and sorted(opp["cards"]) == sorted(deck)


def test_coach_hand_keys(client):
    wait_for(lambda: len(client.get("/api/state").json()["my_deck"]) == 8)
    deck = ["giant", "witch", "zap", "fireball", "mega-minion", "minions", "arrows", "prince"]
    cmd(client, cmd="start")
    cmd(client, cmd="set_opp_deck", cards=deck)
    assert cmd(client, cmd="coach_hand", slot=0)["ignored"]  # coach mode is off
    cmd(client, cmd="set_coach", on=True)

    # 8 known cards, none played: the hand could be any 4 of them.
    r = client.post("/api/command", json={"cmd": "coach_hand", "slot": 0})
    assert r.status_code == 400 and "isn't fully known" in r.json()["detail"]

    for card in deck[:4]:  # 4 played, so the other 4 are exactly the hand
        cmd(client, cmd="play", side="opp", card=card)
    hand = client.get("/api/state").json()["match"]["opp"]["cycle"]["hand"]
    assert sorted(hand) == sorted(deck[4:])
    assert cmd(client, cmd="coach_hand", slot=2)["card"] == hand[2]  # E = 3rd card shown


def test_coach_backspace_undo(client):
    wait_for(lambda: len(client.get("/api/state").json()["my_deck"]) == 8)
    cmd(client, cmd="start")
    cmd(client, cmd="play", side="opp", card="giant")
    assert cmd(client, cmd="coach_undo")["ignored"]  # off outside coach mode: Backspace is everywhere
    cmd(client, cmd="set_coach", on=True)
    assert cmd(client, cmd="coach_undo")["undone"]["card"] == "giant"
    assert client.get("/api/state").json()["match"]["opp"]["revealed"] == []


def test_coach_hand_key_matches_what_was_shown(client):
    wait_for(lambda: len(client.get("/api/state").json()["my_deck"]) == 8)
    deck = ["hog-rider", "cannon", "musketeer", "ice-golem", "fisherman", "lightning", "electro-spirit", "the-log"]
    cmd(client, cmd="start")
    cmd(client, cmd="set_coach", on=True)
    cmd(client, cmd="set_opp_deck", cards=deck)
    for card in deck[:4]:
        cmd(client, cmd="play", side="opp", card=card)
    shown = client.get("/api/state").json()["match"]["opp"]["cycle"]["hand"]
    played = cmd(client, cmd="coach_hand", slot=2)["card"]  # E
    assert played == shown[2]
    after = client.get("/api/state").json()["match"]["opp"]["cycle"]["hand"]
    # The other three keep their keys; the incoming card takes E.
    assert [after[i] for i in (0, 1, 3)] == [shown[i] for i in (0, 1, 3)] and after[2] == "hog-rider"


def test_tap_keys_saved_and_validated(client, tmp_path):
    taps = [{"key": "Q", "x": 0.25, "y": 0.9, "name": "Card 1"}, {"key": "space", "x": 0.5, "y": 0.5}]
    saved = cmd(client, cmd="set_taps", taps=taps)["taps"]
    assert saved == [{"key": "q", "x": 0.25, "y": 0.9, "name": "Card 1"},
                     {"key": "space", "x": 0.5, "y": 0.5, "name": ""}]
    assert client.get("/api/state").json()["phone_taps"] == saved
    for bad in ([{"key": "q", "x": 1.5, "y": 0}], [{"key": "q", "x": 0, "y": 0}, {"key": "Q", "x": 1, "y": 1}],
                [{"key": "", "x": 0, "y": 0}], "nope"):
        assert client.post("/api/command", json={"cmd": "set_taps", "taps": bad}).status_code == 400
    # They survive a restart.
    assert Harness(client.harness.cfg).taps == saved
