import io
import json

import httpx
import pytest
from PIL import Image

from clasharness.predict.crawler import ingest_battlelog
from clasharness.vision.capture import crop
from clasharness.vision.reader import BannerReader, ReadError, parse_reply
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


def openrouter(reply: dict, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json=reply)
    return httpx.MockTransport(handler)


def completion(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


async def test_reader_parses_openrouter_reply():
    seen = []
    reply = completion('{"player_name": "lone wolf ☆", "clan_name": "", "confident": true}')
    reader = BannerReader("deepseek/deepseek-v4.1-flash", "key", transport=openrouter(reply, seen=seen))
    out = await reader.read(b"\x89PNG...")
    assert out.player_name == "lone wolf ☆" and out.clan_name == ""
    body = seen[0]
    assert body["model"] == "deepseek/deepseek-v4.1-flash"
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["response_format"]["type"] == "json_schema"


def test_parse_reply_tolerates_code_fences():
    out = parse_reply('Sure:\n```json\n{"player_name": "Bob", "clan_name": "Clan", "confident": false}\n```')
    assert out.player_name == "Bob" and not out.confident


async def test_reader_surfaces_api_errors():
    reader = BannerReader("m", "key", transport=openrouter({"error": {"code": 402, "message": "Insufficient credits"}}, 402))
    with pytest.raises(ReadError, match="Insufficient credits"):
        await reader.read(b"x")


def test_reader_needs_key():
    with pytest.raises(ReadError):
        BannerReader("m", None)
