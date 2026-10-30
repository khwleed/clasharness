"""Predict an opponent's unseen cards from the cards they've played so far.

Meta model: among known decks that contain every card seen (S), how often does
each other card appear? P(c | S) = w(decks ⊇ S ∪ {c}) / w(decks ⊇ S).
When too few decks contain all of S (an off-meta deck, or a sparse dataset),
fall back to decks weighted by how much of S they contain.

If we know who the opponent is, their own recent decks are blended in with a
high weight, since most players stick to one or two decks.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Prediction:
    cards: list[tuple[str, float]]  # (card, probability it's in the deck), best first
    decks: list[dict]  # likely full decks: {"cards", "missing", "share", "source"}
    mode: str  # "exact" | "similar" | "history" | "none"
    support: float  # weight of decks behind the meta estimate

    def to_dict(self) -> dict:
        return {
            "cards": [{"card": c, "p": round(p, 4)} for c, p in self.cards],
            "decks": self.decks,
            "mode": self.mode,
            "support": round(self.support, 1),
        }


class DeckIndex:
    def __init__(self, decks: list[tuple[frozenset[str], float]]):
        self.decks = decks
        self.by_card: dict[str, set[int]] = defaultdict(set)
        for i, (cards, _) in enumerate(decks):
            for c in cards:
                self.by_card[c].add(i)

    @classmethod
    def from_rows(cls, rows: list[tuple[str, int]]) -> "DeckIndex":
        return cls([(frozenset(k.split(",")), float(n)) for k, n in rows])

    def __len__(self) -> int:
        return len(self.decks)

    def weighted_matches(self, seen: frozenset[str], min_support: float) -> tuple[dict[int, float], str, float]:
        """Deck index -> weight, plus which mode produced them and the exact-match support."""
        if not self.decks:
            return {}, "none", 0.0
        if not seen:
            return {i: w for i, (_, w) in enumerate(self.decks)}, "exact", sum(w for _, w in self.decks)
        sets = sorted((self.by_card.get(c, set()) for c in seen), key=len)
        exact = set.intersection(*sets) if sets else set()
        support = sum(self.decks[i][1] for i in exact)
        if support >= min_support:
            return {i: self.decks[i][1] for i in exact}, "exact", support
        overlap: dict[int, int] = defaultdict(int)
        for c in seen:
            for i in self.by_card.get(c, ()):
                overlap[i] += 1
        n = len(seen)
        # Cubed overlap fraction: a deck sharing 3 of 4 seen cards counts ~0.42, 2 of 4 ~0.13.
        weights = {i: self.decks[i][1] * (k / n) ** 3 for i, k in overlap.items()}
        return weights, ("similar" if weights else "none"), support


def _card_probs(index: DeckIndex, weights: dict[int, float], seen: frozenset[str]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return {}
    acc: dict[str, float] = defaultdict(float)
    for i, w in weights.items():
        for c in index.decks[i][0]:
            if c not in seen:
                acc[c] += w
    return {c: v / total for c, v in acc.items()}


def _top_decks(index: DeckIndex, weights: dict[int, float], seen: frozenset[str], source: str, n: int) -> list[dict]:
    total = sum(weights.values()) or 1.0
    best = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [{
        "cards": sorted(index.decks[i][0]),
        "missing": sorted(index.decks[i][0] - seen),
        "contradicts": sorted(seen - index.decks[i][0]),
        "share": round(w / total, 4),
        "source": source,
    } for i, w in best]


def predict(meta: DeckIndex, seen: list[str] | set[str], history: list[list[str]] | None = None,
            min_support: float = 15, history_weight: float = 0.8, top_decks: int = 5) -> Prediction:
    S = frozenset(seen)
    if len(S) >= 8:
        return Prediction([], [], "complete", 0)

    meta_w, mode, support = meta.weighted_matches(S, min_support)
    probs = _card_probs(meta, meta_w, S)
    decks = _top_decks(meta, meta_w, S, "meta", top_decks)

    if history:
        hist = DeckIndex([(frozenset(d), 1.0) for d in history if len(d) == 8])
        hist_w, hist_mode, _ = hist.weighted_matches(S, min_support=1)
        if hist_w:
            hist_probs = _card_probs(hist, hist_w, S)
            # Trust their own history fully only if a past deck contains everything seen so far.
            lam = history_weight if hist_mode == "exact" else history_weight * 0.4
            if not probs:
                lam = 1.0
            keys = set(probs) | set(hist_probs)
            probs = {c: lam * hist_probs.get(c, 0.0) + (1 - lam) * probs.get(c, 0.0) for c in keys}
            decks = _top_decks(hist, hist_w, S, "history", 3) + decks[: max(top_decks - 3, 2)]
            mode = "history" if hist_mode == "exact" else mode

    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    return Prediction(ranked, decks, mode if probs else "none", support)
