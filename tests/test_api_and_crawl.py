from clasharness.api import normalize_tag, parse_battle
from clasharness.predict.crawler import crawl

from .conftest import load


def test_normalize_tag():
    assert normalize_tag("abc0o") == "#ABC00"
    assert normalize_tag("#2PP") == "#2PP"


def test_parse_battle_skips_2v2():
    log = load("battlelog_me.json")
    parsed = [parse_battle(b) for b in log]
    assert parsed[2] is None
    b = parsed[0]
    assert b["ranked"] and b["sides"][1]["name"] == "Giant Guy"
    assert b["sides"][1]["cards"][0] == "giant"
    assert parsed[1]["sides"][1]["clan_name"] is None


async def test_crawl_ingests_decks_and_players(api, db, fake_server):
    stats = await crawl(api, db, ["#ME123"], max_players=5)
    assert stats["players"] >= 1
    s = db.stats()
    assert s["battles"] == 2  # 2v2 skipped
    assert s["decks"] == 3  # my deck once (deduped by key) + two opponents
    assert db.get_player("#NOCLAN9")["name"] == "lone wolf ☆"
    # Re-crawling the same log doesn't double count.
    await crawl(api, db, ["#ME123"], max_players=1)
    assert db.stats()["battles"] == 2
