"""Card-cycle inference.

The deck is a queue of 8; the hand is the first 4 slots and the 5th is "next".
Playing a card sends it to the back. We never see the starting order, but every
card that has been played sits behind every card that hasn't, ordered by when it
was last played. So the queue is always:

    [unplayed cards, order unknown] + [played cards, least recently played first]

which lets us pin down the hand exactly once at most 4 cards are still unplayed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DECK_SIZE = 8
HAND_SIZE = 4


@dataclass
class CycleView:
    hand: list[str]  # cards known to be in hand right now
    hand_unknown: int  # hand slots we can't name
    hand_candidates: list[str]  # known cards that might fill those slots
    next: str | None  # the card that comes in after the next play, if known
    queue: list[str]  # played cards in queue order (front first), positions >= unplayed count
    not_in_hand: list[str]  # cards we know they can't have in hand
    unplayed_known: list[str]
    unseen_slots: int  # deck slots whose card we've never seen
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def infer(deck: list[str], plays: list[str], deck_size: int = DECK_SIZE) -> CycleView:
    """deck: cards known to be in the deck (all 8 for me, revealed cards for the opponent).
    plays: every card played, in order."""
    warnings: list[str] = []
    played_order: list[str] = []  # distinct cards, least recently played first
    for i, card in enumerate(plays):
        if card in played_order:
            pos = played_order.index(card)
            unplayed = deck_size - len(played_order)
            if unplayed + pos >= HAND_SIZE:
                warnings.append(f"play #{i + 1} ({card}) shouldn't have been in hand yet")
            played_order.remove(card)
        played_order.append(card)

    known = list(dict.fromkeys([*deck, *played_order]))
    unplayed_known = [c for c in known if c not in played_order]
    unseen_slots = max(deck_size - len(known), 0)
    u = len(unplayed_known) + unseen_slots  # cards not yet cycled

    if u >= HAND_SIZE:
        if u == HAND_SIZE and unseen_slots == 0:
            hand, hand_unknown, candidates = unplayed_known, 0, []
        else:
            hand, hand_unknown, candidates = [], HAND_SIZE, unplayed_known
        nxt = played_order[0] if u == HAND_SIZE and played_order else None
    else:
        hand = unplayed_known + played_order[: HAND_SIZE - u]
        hand_unknown = unseen_slots
        candidates = []
        nxt = played_order[HAND_SIZE - u] if HAND_SIZE - u < len(played_order) else None

    in_hand_possible = set(hand) | set(candidates)
    not_in_hand = [c for c in played_order if c not in in_hand_possible]
    return CycleView(hand, hand_unknown, candidates, nxt, played_order, not_in_hand, unplayed_known, unseen_slots, warnings)
