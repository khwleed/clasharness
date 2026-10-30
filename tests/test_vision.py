import io
from types import SimpleNamespace

import pytest
from PIL import Image

from clasharness.predict.crawler import ingest_battlelog
from clasharness.vision.capture import crop
from clasharness.vision.reader import BannerRead, BannerReader, ReadError
from clasharness.vision.resolve import resolve


async def test_resolves_unclanned_player_from_local_index(api, db):
    await ingest_battlelog(api, db, "#ME123")
    res = await resolve(api, db, "lone wolf ☆", "")
    assert res.status == "resolved"
    assert res.tag == "#NOCLAN9"


async def test_resolves_via_clan_search_when_not_indexed(api, db, fake_server):
    res = await resolve(api, db, "Giant Guy", "Big Clan")
    assert res.status == "resolved" and res.tag == "#OPP111"
    assert any(p.startswith("/clans") for p in fake_server.calls)
    # Clan members are now in the index for next time.
    assert db.get_player("#OPP222")["name"] == "Someone Else"


async def test_ambiguous_when_two_close_names(api, db):
    db.upsert_player({"tag": "#A1", "name": "Bob", "trophies": 7000})
    db.upsert_player({"tag": "#A2", "name": "Bob", "trophies": 7100})
    res = await resolve(api, db, "Bob", "")
    assert res.status == "ambiguous"
    assert {c.tag for c in res.candidates} == {"#A1", "#A2"}


async def test_not_found_gives_search_link(api, db):
    res = await resolve(api, db, "Nobody Here", "")
    assert res.status == "not_found"
    assert "Nobody%20Here" in res.search_url


def test_crop_uses_fractions():
    buf = io.BytesIO()
    Image.new("RGB", (1000, 2000)).save(buf, "PNG")
    out = Image.open(io.BytesIO(crop(buf.getvalue(), [0, 0, 0.5, 0.1], upscale=1)))
    assert out.size == (500, 200)


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


async def test_reader_returns_parsed_output():
    parsed = BannerRead(player_name="lone wolf ☆", clan_name="", confident=True)
    client, messages = fake_client(SimpleNamespace(stop_reason="end_turn", parsed_output=parsed))
    out = await BannerReader("claude-opus-5-5", client=client).read(b"\x89PNG...")
    assert out.player_name == "lone wolf ☆"
    assert messages.kwargs["output_format"] is BannerRead
    assert messages.kwargs["messages"][0]["content"][0]["type"] == "image"


async def test_reader_surfaces_refusal():
    client, _ = fake_client(SimpleNamespace(stop_reason="refusal", parsed_output=None))
    with pytest.raises(ReadError):
        await BannerReader("claude-opus-5-5", client=client).read(b"x")
