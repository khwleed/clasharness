import pytest

from clasharness.cards import Catalog
from clasharness.config import DEFAULTS
from clasharness.match.elixir import Rules
from clasharness.match.state import Match

DECK = ["hog-rider", "musketeer", "fireball", "the-log", "ice-golem", "skeletons", "ice-spirit", "cannon"]


@pytest.fixture
def match():
    m = Match(DECK, Rules.from_config(DEFAULTS["elixir"]), Catalog.load())
    m.add("start", t=0)
    return m


def test_snapshot_tracks_both_sides(match):
    match.add("play", "me", "hog-rider", t=1)
    match.add("play", "opp", "giant", t=2)
    s = match.snapshot(now=2)
    assert s["me"]["elixir"]["value"] == pytest.approx(5 + 2 / 2.8 - 4, abs=1e-3)
    assert s["opp"]["elixir"]["value"] == pytest.approx(5 + 2 / 2.8 - 5, abs=1e-3)
    assert s["opp"]["revealed"] == ["giant"]


def test_undo_restores_elixir(match):
    match.add("play", "me", "hog-rider", t=1)
    match.undo()
    assert match.snapshot(now=1)["me"]["elixir"]["value"] == pytest.approx(5 + 1 / 2.8, abs=1e-3)
    assert match.undo() is None  # start event is never undone


def test_mirror_costs_previous_card_plus_one(match):
    match.add("play", "opp", "fireball", t=10)
    match.add("play", "opp", "mirror", t=10)
    assert match.play_cost("opp", 1) == 5


def test_champion_ability(match):
    match.my_deck = [*DECK[:7], "archer-queen"]
    match.add("ability", "me", t=0)
    assert match.snapshot(now=0)["me"]["elixir"]["value"] == pytest.approx(4)


def test_next_phase_shifts_clock(match):
    match.add("next_phase", t=5)
    s = match.snapshot(now=5)
    assert s["phase"]["multiplier"] == 2
    assert s["game_time"] == 120


def test_end_freezes_time(match):
    match.add("end", t=10)
    assert match.snapshot(now=100)["game_time"] == 10
    with pytest.raises(ValueError):
        match.add("play", "me", "cannon", t=11)
