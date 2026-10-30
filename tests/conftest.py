import json
from pathlib import Path

import httpx
import pytest

from clasharness.api import ClashApi
from clasharness.db import Database

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeClashServer:
    """Routes for httpx.MockTransport, recording every request path."""

    def __init__(self):
        self.calls: list[str] = []
        self.battlelogs = {"#ME123": load("battlelog_me.json")}
        self.players = {"#ME123": {
            "tag": "#ME123", "name": "Me", "trophies": 7000, "clan": {"tag": "#CLAN1", "name": "My Clan"},
            "currentDeck": [{"name": n} for n in ["Hog Rider", "Musketeer", "Fireball", "The Log",
                                                    "Ice Golem", "Skeletons", "Ice Spirit", "Cannon"]],
        }}
        self.clans = {"Big Clan": [{"tag": "#CLAN2", "name": "Big Clan"}, {"tag": "#CLAN3", "name": "Big Clan Two"}]}
        self.members = {
            "#CLAN2": [{"tag": "#OPP111", "name": "Giant Guy", "trophies": 7020},
                       {"tag": "#OPP222", "name": "Someone Else", "trophies": 6900}],
            "#CLAN3": [{"tag": "#OPP333", "name": "Giant Gal", "trophies": 5000}],
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        self.calls.append(path)
        from urllib.parse import unquote
        parts = [unquote(p) for p in path.strip("/").split("/")]
        if parts == ["cards"]:
            return httpx.Response(200, json=json.loads((Path(__file__).parents[1] / "src/clasharness/data/cards_fallback.json").read_text()))
        if parts[0] == "players" and len(parts) == 3 and parts[2] == "battlelog":
            return httpx.Response(200, json=self.battlelogs.get(parts[1], []))
        if parts[0] == "players" and len(parts) == 2:
            p = self.players.get(parts[1])
            return httpx.Response(200, json=p) if p else httpx.Response(404, json={"reason": "notFound"})
        if parts == ["clans"]:
            return httpx.Response(200, json={"items": self.clans.get(request.url.params["name"], [])})
        if parts[0] == "clans" and parts[2] == "members":
            return httpx.Response(200, json={"items": self.members.get(parts[1], [])})
        return httpx.Response(404, json={"reason": "notFound"})


@pytest.fixture
def fake_server():
    return FakeClashServer()


@pytest.fixture
def api(fake_server):
    return ClashApi("test-token", "https://proxy.example/v1", requests_per_second=0,
                    transport=httpx.MockTransport(fake_server))


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    yield d
    d.close()
