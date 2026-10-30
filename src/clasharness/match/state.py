"""Event-sourced match state.

A match is a list of events. Everything shown in the UI is derived from that
list by `snapshot()`, so undo is just dropping the last event and the same list
is what gets saved as match history.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Literal

from ..cards import Catalog
from . import cycle
from .elixir import Delta, GameClock, Rules, collector_ticks, simulate

Side = Literal["me", "opp"]
SIDES: tuple[Side, Side] = ("me", "opp")

EVENT_TYPES = {"start", "play", "adjust", "ability", "collector_destroyed", "next_phase", "end"}


@dataclass
class Event:
    type: str
    t: float
    side: Side | None = None
    card: str | None = None
    amount: float | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(**{k: d.get(k) for k in ("type", "t", "side", "card", "amount")})


def other(side: Side) -> Side:
    return "opp" if side == "me" else "me"


@dataclass
class Match:
    my_deck: list[str]
    rules: Rules
    catalog: Catalog
    events: list[Event] = field(default_factory=list)
    opp_tag: str | None = None
    opp_name: str | None = None

    # ---- mutation -------------------------------------------------------
    def add(self, type_: str, side: Side | None = None, card: str | None = None,
            amount: float | None = None, t: float | None = None) -> Event:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_!r}")
        if type_ != "start" and not self.started:
            raise ValueError("match hasn't started")
        if self.ended and type_ != "end":
            raise ValueError("match is over")
        if type_ == "start" and self.started:
            raise ValueError("match already started")
        ev = Event(type_, time.time() if t is None else t, side, card, amount)
        self.events.append(ev)
        return ev

    def undo(self) -> Event | None:
        if len(self.events) > 1:  # keep the start event; restart instead
            return self.events.pop()
        return None

    # ---- queries --------------------------------------------------------
    @property
    def started(self) -> bool:
        return bool(self.events) and self.events[0].type == "start"

    @property
    def ended(self) -> bool:
        return any(e.type == "end" for e in self.events)

    @property
    def start_t(self) -> float:
        return self.events[0].t

    @property
    def end_t(self) -> float | None:
        return next((e.t for e in self.events if e.type == "end"), None)

    def plays(self, side: Side) -> list[str]:
        return [e.card for e in self.events if e.type == "play" and e.side == side]

    def opp_revealed(self) -> list[str]:
        """Opponent cards in the order they were first seen (hotkeys Alt+1..8 follow this)."""
        return list(dict.fromkeys(self.plays("opp")))

    def clock(self) -> GameClock:
        syncs = [(self.start_t, 0.0)]
        for e in self.events:
            if e.type == "next_phase":
                g = GameClock(syncs).game_time(e.t)
                nxt = self.rules.next_phase_start(g)
                if nxt is not None:
                    syncs.append((e.t, nxt))
        return GameClock(syncs)

    def play_cost(self, side: Side, index: int) -> float:
        """Cost of the side's index-th play, resolving Mirror against the card before it."""
        plays = self.plays(side)
        card = plays[index]
        mirror = self.catalog.overrides.get("mirror", {})
        if card == mirror.get("key", "mirror"):
            prev = plays[index - 1] if index > 0 else None
            return (self.catalog.cost(prev) if prev else 0) + mirror.get("extra_cost", 1)
        return self.catalog.cost(card)

    def champion(self, side: Side) -> str | None:
        cards = self.my_deck if side == "me" else self.opp_revealed()
        return next((c for c in cards if (card := self.catalog.get(c)) and card.is_champion), None)

    def deltas(self, side: Side) -> list[Delta]:
        out: list[Delta] = []
        collector_cfg = self.catalog.overrides.get("elixir_collector")
        collectors: list[list[float | None]] = []
        play_i = 0
        for e in self.events:
            if e.type == "play" and e.side == side:
                out.append(Delta(e.t, -self.play_cost(side, play_i), e.card or ""))
                play_i += 1
                if collector_cfg and e.card == collector_cfg["key"]:
                    collectors.append([e.t, None])
            elif e.type == "adjust" and e.side == side:
                out.append(Delta(e.t, e.amount or 0.0, "adjust"))
            elif e.type == "ability" and e.side == side:
                champ = self.champion(side)
                cost = e.amount if e.amount is not None else (self.catalog.ability_cost(champ) if champ else None)
                out.append(Delta(e.t, -(cost or 0), f"ability:{champ}"))
            elif e.type == "collector_destroyed" and e.side == side:
                live = [c for c in collectors if c[1] is None]
                if live:
                    live[0][1] = e.t
        for placed, destroyed in collectors:
            out.extend(collector_ticks(placed, destroyed, collector_cfg))
        return out

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        if not self.started:
            return {"status": "idle", "my_deck": self.my_deck}
        if self.ended:
            now = min(now, self.end_t)
        clock = self.clock()
        g = clock.game_time(now)
        phase = self.rules.phase_at(g)
        reg = self.rules.regulation_seconds
        sides = {}
        for side in SIDES:
            deck = self.my_deck if side == "me" else self.opp_revealed()
            view = cycle.infer(deck, self.plays(side))
            sides[side] = {
                "elixir": simulate(self.rules, clock, self.start_t, self.deltas(side), now).to_dict(),
                "cycle": view.to_dict(),
                "champion": self.champion(side),
            }
        sides["opp"]["revealed"] = self.opp_revealed()
        return {
            "status": "ended" if self.ended else "running",
            "game_time": round(g, 2),
            "clock_remaining": round(self._remaining(g), 1),
            "overtime": g >= reg,
            "phase": {"name": phase.name, "multiplier": phase.multiplier},
            "my_deck": self.my_deck,
            "me": sides["me"],
            "opp": sides["opp"],
            "events": [self._describe(e, clock) for e in self.events],
        }

    def _describe(self, e: Event, clock: GameClock) -> dict:
        d = e.to_dict()
        g = clock.game_time(e.t)
        d["game_time"] = round(g, 1)
        d["clock"] = round(self._remaining(g), 1)  # what the in-game clock showed
        return d

    def _remaining(self, g: float) -> float:
        reg = self.rules.regulation_seconds
        return max((reg - g) if g < reg else (self.rules.end - g), 0.0)
