"""Tests for the per-case improvements: package risk, fill counting, hysteresis, leg repair, etc."""

from ritc.core.execution import Executor
from ritc.core.risk import LimitGroup, RiskManager

W = {"A": 1.0, "B": 1.0}


# ------------------------------------------------------------------ core


def test_room_package_allows_reducing_a_breached_limit():
    r = RiskManager(groups=[LimitGroup("eq", 100, 100, {"F": 1, "S": 1})], buffer=1.0)
    pos = {"F": 80, "S": -80}                          # gross 160 > 100 already
    assert r.room_package({"F": 1, "S": -1}, pos) == 0
    assert r.room_package({"F": -1, "S": 1}, pos) >= 80
    r.halted = True
    assert r.room_package({"F": -1, "S": 1}, pos) == 0


def test_filled_counts_fills_not_orders():
    ex = Executor(client=None, dry_run=False)
    assert ex.filled([{"quantity_filled": 30}, {"quantity_filled": 0}, "junk"], 100) == 30
    ex.dry_run = True
    assert ex.filled([], 100) == 100


# ------------------------------------------------------------------ risk management
def test_throttle_ramps_between_soft_start_and_kill():
    from ritc.core.risk import DrawdownGuard
    g = DrawdownGuard(max_drawdown=1000, soft_start=0.5, floor=0.25)
    assert g.throttle() == 1.0
    g.update(5000)
    g.update(4600)                                     # 400 down: below soft start
    assert g.throttle() == 1.0
    g.update(4250)                                     # 750 down: halfway through the ramp
    assert abs(g.throttle() - 0.625) < 1e-9
    g.update(6000)                                     # new peak: full size again
    assert g.throttle() == 1.0
    assert g.update(5000) and g.throttle() == 0.0      # 1000 down: kill switch


def test_throttle_shrinks_limits_but_never_blocks_reducing():
    r = RiskManager(max_position={"X": 10_000}, buffer=1.0)
    r.throttle = 0.5
    assert r.room("X", "BUY", {"X": 0}) == 5_000
    assert r.room("X", "BUY", {"X": 8_000}) == 0
    assert r.room("X", "SELL", {"X": 8_000}) >= 8_000


def test_lockstep_simulator_is_reproducible():
    from ritc.tune import run_one
    a = run_one("liquidity", {}, seed=3, speed=0)
    b = run_one("liquidity", {}, seed=3, speed=0)
    assert abs(a - b) < 0.01                            # concurrent legs: float summation order only
