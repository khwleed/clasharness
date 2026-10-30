import pytest

from clasharness.config import DEFAULTS
from clasharness.match.elixir import Delta, GameClock, Rules, collector_ticks, regen_between, simulate

RULES = Rules.from_config(DEFAULTS["elixir"])


def clock():
    return GameClock([(0.0, 0.0)])


def test_single_elixir_rate():
    assert regen_between(RULES, clock(), 0, 28) == pytest.approx(10)


def test_rate_doubles_at_two_minutes_and_triples_in_last_overtime_minute():
    assert regen_between(RULES, clock(), 120, 120 + 14) == pytest.approx(10)
    assert regen_between(RULES, clock(), 180, 190) == pytest.approx(20 / 2.8)
    assert regen_between(RULES, clock(), 240, 250) == pytest.approx(30 / 2.8)


def test_regen_spans_phase_boundary():
    # 10s at x1 then 10s at x2
    assert regen_between(RULES, clock(), 110, 130) == pytest.approx(30 / 2.8)


def test_no_regen_after_match_end():
    assert regen_between(RULES, clock(), 300, 400) == 0


def test_start_cap_and_leak():
    s = simulate(RULES, clock(), 0, [], 28)
    assert s.value == pytest.approx(10)
    assert s.leaked == pytest.approx(14 / 2.8)  # started at 5, full after 14s


def test_play_subtracts_cost():
    s = simulate(RULES, clock(), 0, [Delta(0, -4)], 0)
    assert s.value == pytest.approx(1)
    s = simulate(RULES, clock(), 0, [Delta(0, -4)], 2.8)
    assert s.value == pytest.approx(2)


def test_overspend_counts_deficit_and_clamps():
    s = simulate(RULES, clock(), 0, [Delta(1, -9)], 1)
    assert s.value == 0
    assert s.deficits == 1


def test_clock_sync_jumps_phase():
    c = GameClock([(0.0, 0.0), (10.0, 120.0)])  # user forced double elixir at 10s
    assert c.game_time(20) == 130
    assert regen_between(RULES, c, 10, 24) == pytest.approx(10)


def test_collector_ticks_stop_when_destroyed():
    cfg = {"interval_seconds": 9.0, "lifetime_seconds": 86.0, "first_after_seconds": 9.0}
    assert len(collector_ticks(0, None, cfg)) == 9
    assert [d.t for d in collector_ticks(0, 20, cfg)] == [9, 18]
