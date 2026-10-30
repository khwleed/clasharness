from clasharness.match.cycle import infer

DECK = ["hog-rider", "musketeer", "fireball", "the-log", "ice-golem", "skeletons", "ice-spirit", "cannon"]


def test_fresh_deck_hand_unknown():
    v = infer(DECK, [])
    assert v.hand == [] and v.hand_unknown == 4
    assert set(v.hand_candidates) == set(DECK)
    assert v.next is None


def test_hand_known_exactly_after_four_plays():
    plays = ["hog-rider", "musketeer", "fireball", "the-log"]
    v = infer(DECK, plays)
    assert set(v.hand) == {"ice-golem", "skeletons", "ice-spirit", "cannon"}
    assert v.hand_unknown == 0
    assert v.next == "hog-rider"
    assert v.not_in_hand == plays


def test_cycle_after_five_plays():
    plays = ["hog-rider", "musketeer", "fireball", "the-log", "ice-golem"]
    v = infer(DECK, plays)
    assert set(v.hand) == {"skeletons", "ice-spirit", "cannon", "hog-rider"}
    assert v.next == "musketeer"


def test_replaying_a_card_moves_it_to_the_back():
    plays = ["hog-rider", "musketeer", "fireball", "the-log", "ice-golem", "hog-rider"]
    v = infer(DECK, plays)
    assert v.queue == ["musketeer", "fireball", "the-log", "ice-golem", "hog-rider"]
    assert set(v.hand) == {"skeletons", "ice-spirit", "cannon", "musketeer"}
    assert v.warnings == []


def test_impossible_replay_warns():
    v = infer(DECK, ["hog-rider", "musketeer", "hog-rider"])
    assert v.warnings


def test_opponent_partial_knowledge():
    # Opponent: only played cards are known.
    v = infer([], ["giant", "witch"])
    assert v.unseen_slots == 6
    assert v.hand == [] and v.hand_unknown == 4
    assert v.not_in_hand == ["giant", "witch"]


def test_opponent_after_six_distinct_plays():
    plays = ["a", "b", "c", "d", "e", "f"]
    v = infer([], plays)
    # 2 unseen cards are in hand plus the 2 least recently played
    assert v.hand == ["a", "b"]
    assert v.hand_unknown == 2
    assert v.next == "c"
    assert v.not_in_hand == ["c", "d", "e", "f"]
