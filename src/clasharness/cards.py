"""Card catalog: names, elixir costs and icons, keyed by a stable slug."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from rapidfuzz import fuzz, process, utils

from .config import DATA_DIR


def slugify(name: str) -> str:
    """'Mini P.E.K.K.A' -> 'mini-pekka', 'X-Bow' -> 'x-bow'."""
    s = name.lower().replace(".", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


@dataclass(frozen=True)
class Card:
    key: str
    name: str
    elixir: int | None
    rarity: str = ""
    icon: str | None = None
    icon_evo: str | None = None
    id: int | None = None

    @property
    def is_champion(self) -> bool:
        return self.rarity == "champion"

    def to_dict(self) -> dict:
        return asdict(self)


class Catalog:
    def __init__(self, cards: list[Card], overrides: dict | None = None):
        self.cards = {c.key: c for c in cards}
        self.overrides = overrides if overrides is not None else load_overrides()
        self._names = {c.key: c.name for c in cards}

    @classmethod
    def from_api_payload(cls, payload: dict, overrides: dict | None = None) -> "Catalog":
        cards = []
        for item in payload.get("items", []):
            icons = item.get("iconUrls") or {}
            cards.append(Card(
                key=slugify(item["name"]),
                name=item["name"],
                elixir=item.get("elixirCost"),
                rarity=(item.get("rarity") or "").lower(),
                icon=icons.get("medium"),
                icon_evo=icons.get("evolutionMedium"),
                id=item.get("id"),
            ))
        return cls(cards, overrides)

    @classmethod
    def load(cls, cache_file: Path | None = None) -> "Catalog":
        """Cached API catalog if present, else the bundled offline fallback."""
        for p in (cache_file, DATA_DIR / "cards_fallback.json"):
            if p and p.is_file():
                return cls.from_api_payload(json.loads(p.read_text()))
        return cls([])

    def __contains__(self, key: str) -> bool:
        return key in self.cards

    def get(self, key: str) -> Card | None:
        return self.cards.get(key)

    def key_for(self, name: str) -> str:
        return slugify(name)

    def cost(self, key: str) -> int:
        card = self.cards.get(key)
        return card.elixir if card and card.elixir is not None else 0

    def ability_cost(self, key: str) -> int | None:
        return self.overrides.get("champion_ability_cost", {}).get(key)

    def search(self, query: str, limit: int = 8, exclude: set[str] = frozenset()) -> list[Card]:
        choices = {k: n for k, n in self._names.items() if k not in exclude}
        hits = process.extract(query, choices, scorer=fuzz.WRatio, processor=utils.default_process, limit=limit)
        return [self.cards[key] for _, _, key in hits]

    def to_list(self) -> list[dict]:
        return [c.to_dict() for c in sorted(self.cards.values(), key=lambda c: c.name)]


def load_overrides() -> dict:
    return json.loads((DATA_DIR / "card_overrides.json").read_text())
