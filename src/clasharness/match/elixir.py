"""Time-based elixir model.

Everything here is a pure function of the match's events, so the value at any
instant can be recomputed from scratch (which is what makes undo trivial).
Times are seconds on the wall clock; "game time" is seconds since "Fight!" and
can be shifted by clock syncs (e.g. the user forcing the next phase).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Phase:
    start: float
    multiplier: float
    name: str


@dataclass
class Rules:
    start: float = 5.0
    max: float = 10.0
    seconds_per_elixir: float = 2.8
    phases: list[Phase] = field(default_factory=lambda: [Phase(0, 1, "Single elixir")])
    regulation_seconds: float = 180
    end: float = 300

    @classmethod
    def from_config(cls, cfg: dict) -> "Rules":
        phases = sorted((Phase(p["start"], p["multiplier"], p["name"]) for p in cfg["phases"]), key=lambda p: p.start)
        return cls(cfg["start"], cfg["max"], cfg["seconds_per_elixir"], phases, cfg["regulation_seconds"], cfg["end"])

    def phase_at(self, game_time: float) -> Phase:
        current = self.phases[0]
        for p in self.phases:
            if game_time >= p.start:
                current = p
        return current

    def next_phase_start(self, game_time: float) -> float | None:
        for p in self.phases:
            if p.start > game_time:
                return p.start
        return None


class GameClock:
    """Maps wall time to game time through a list of (wall_t, game_t) syncs."""

    def __init__(self, syncs: list[tuple[float, float]]):
        if not syncs:
            raise ValueError("clock needs at least the match-start sync")
        self.syncs = sorted(syncs)

    def game_time(self, t: float) -> float:
        base_t, base_g = self.syncs[0]
        for st, sg in self.syncs:
            if st <= t:
                base_t, base_g = st, sg
        return base_g + (t - base_t)

    def sync_times(self) -> list[float]:
        return [t for t, _ in self.syncs]


def regen_between(rules: Rules, clock: GameClock, a: float, b: float) -> float:
    """Elixir generated (uncapped) between wall times a < b."""
    if b <= a:
        return 0.0
    cuts = sorted({a, b, *(t for t in clock.sync_times() if a < t < b)})
    total = 0.0
    for s, e in zip(cuts, cuts[1:]):
        g0 = clock.game_time(s)
        g1 = g0 + (e - s)  # linear inside a sync segment
        bounds = sorted({g0, g1, *(p.start for p in rules.phases if g0 < p.start < g1), *([rules.end] if g0 < rules.end < g1 else [])})
        for x, y in zip(bounds, bounds[1:]):
            if x >= rules.end:
                continue
            total += (y - x) * rules.phase_at(x).multiplier / rules.seconds_per_elixir
    return total


@dataclass(frozen=True)
class Delta:
    t: float
    amount: float
    reason: str = ""


@dataclass
class ElixirState:
    value: float
    leaked: float
    deficits: int  # times a play cost more than we thought they had: a sign of drift
    rate: float  # elixir per second right now

    def to_dict(self) -> dict:
        return {"value": round(self.value, 3), "leaked": round(self.leaked, 2), "deficits": self.deficits, "rate": round(self.rate, 4)}


def collector_ticks(placed_at: float, destroyed_at: float | None, cfg: dict) -> list[Delta]:
    """Elixir Collector pumps: +1 every interval during its lifetime."""
    out = []
    t = placed_at + cfg["first_after_seconds"]
    end = placed_at + cfg["lifetime_seconds"]
    if destroyed_at is not None:
        end = min(end, destroyed_at)
    while t <= end + 1e-9:
        out.append(Delta(t, 1.0, "collector"))
        t += cfg["interval_seconds"]
    return out


def simulate(rules: Rules, clock: GameClock, start_t: float, deltas: list[Delta], now: float) -> ElixirState:
    value, leaked, deficits = rules.start, 0.0, 0
    cursor = start_t
    for d in sorted(deltas, key=lambda d: d.t):
        if d.t > now:
            break
        value, leaked = _advance(rules, clock, value, leaked, cursor, d.t)
        cursor = d.t
        value += d.amount
        if value < -1e-6:
            deficits += 1
        value = min(max(value, 0.0), rules.max)
    value, leaked = _advance(rules, clock, value, leaked, cursor, now)
    g = clock.game_time(now)
    rate = 0.0 if g >= rules.end else rules.phase_at(g).multiplier / rules.seconds_per_elixir
    return ElixirState(value, leaked, deficits, rate)


def _advance(rules: Rules, clock: GameClock, value: float, leaked: float, a: float, b: float) -> tuple[float, float]:
    gained = regen_between(rules, clock, a, b)
    value += gained
    if value > rules.max:
        leaked += value - rules.max
        value = rules.max
    return value, leaked
