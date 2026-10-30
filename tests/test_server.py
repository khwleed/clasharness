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
