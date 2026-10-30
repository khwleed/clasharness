import pytest

from clasharness.predict.model import DeckIndex, predict

HOG = ["hog-rider", "musketeer", "fireball", "the-log", "ice-golem", "skeletons", "ice-spirit", "cannon"]
HOG_EB = ["hog-rider", "earthquake", "the-log", "valkyrie", "musketeer", "skeletons", "ice-spirit", "cannon"]
GIANT = ["giant", "witch", "zap", "fireball", "mega-minion", "minions", "arrows", "prince"]


def index(*decks_with_counts):
    return DeckIndex.from_rows([(",".join(sorted(d)), n) for d, n in decks_with_counts])


def test_exact_mode_conditional_probabilities():
    idx = index((HOG, 30), (HOG_EB, 10), (GIANT, 50))
    p = predict(idx, ["hog-rider"], min_support=15)
    probs = dict(p.cards)
    assert p.mode == "exact"
    assert p.support == 40
    assert probs["musketeer"] == pytest.approx(1.0)
    assert probs["fireball"] == pytest.approx(0.75)
    assert probs["earthquake"] == pytest.approx(0.25)
    assert "giant" not in probs
    assert "hog-rider" not in probs  # seen cards are excluded


def test_probabilities_sum_to_remaining_slots():
    idx = index((HOG, 30), (HOG_EB, 10))
    p = predict(idx, ["hog-rider", "the-log"], min_support=1)
    assert sum(pr for _, pr in p.cards) == pytest.approx(6)


def test_falls_back_to_similar_decks():
    idx = index((HOG, 30), (GIANT, 50))
    # A combination no deck has: similarity mode still leans on the hog deck.
    p = predict(idx, ["hog-rider", "musketeer", "witch"], min_support=15)
    assert p.mode == "similar"
    assert p.cards[0][0] in HOG


def test_history_dominates_when_it_contains_seen_cards():
    idx = index((GIANT, 100), (HOG, 5))
    p = predict(idx, ["fireball"], history=[HOG], history_weight=0.8)
    assert p.mode == "history"
    assert p.cards[0][0] in HOG
    assert p.decks[0]["source"] == "history"


def test_empty_dataset():
    p = predict(DeckIndex([]), ["giant"])
    assert p.cards == [] and p.mode == "none"


def test_complete_deck():
    assert predict(index((HOG, 1)), HOG).mode == "complete"
