"""Tests for the per-case improvements: package risk, fill counting, hysteresis, leg repair, etc."""

from conftest import make_book
from ritc.core.execution import Executor
from ritc.core.risk import LimitGroup, RiskManager
from ritc.strategies.derivatives import OptionSpec, option_signal
from ritc.strategies.equity import QuoteParams, compute_quotes, end_of_period_skew
from ritc.strategies.etf import ArbPlan, plan_arb, repair_legs, with_slippage
from ritc.strategies.liability import evaluate_tender

W = {"A": 1.0, "B": 1.0}


# ------------------------------------------------------------------ core
def test_room_package_counts_every_leg_against_gross():
    r = RiskManager(groups=[LimitGroup("eq", 300_000, 200_000, {"E": 1, "A": 1, "B": 1})], buffer=1.0)
    pkg = {"E": -1, "A": 1, "B": 1}                   # sell 1 ETF, buy the basket
    assert r.room("E", "SELL", {}) == 200_000          # per-ticker view: misses the basket legs
    assert r.room_package(pkg, {}) == 100_000          # 3 gross / package, net +1 / package


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


# ------------------------------------------------------------------ derivatives
SPEC = OptionSpec("RTM1C50", True, 50.0, 300)


def _sig(position, iv_book):
    from ritc.pricing.options import bs_price
    px = bs_price(50, 50, 0.08, 0.0, iv_book, True)
    return option_signal(SPEC, 50, px - 0.01, px + 0.01, 0.08, 0.0, forecast=0.30, vol_edge=0.02,
                         full_edge=0.06, max_contracts=60, fee_per_unit=0.0, position=position)


def test_option_size_is_not_trimmed_while_edge_remains():
    assert _sig(0, 0.24).target == 60                  # 6 pts edge: full size
    assert _sig(60, 0.27).target == 60                 # edge shrank to 3 pts: hold, don't trim
    assert _sig(60, 0.299).target == 60                # still on our side
    assert _sig(60, 0.31).target <= 0                  # edge gone (flipped): exit


# ------------------------------------------------------------------ etf
def _books(etf_bid, etf_ask):
    etf = make_book([(etf_bid, 5000), (etf_bid - 0.05, 5000)], [(etf_ask, 5000), (etf_ask + 0.05, 5000)])
    a = make_book([(19.99, 5000), (19.97, 5000)], [(20.01, 5000), (20.03, 5000)])
    b = make_book([(14.99, 5000), (14.97, 5000)], [(15.01, 5000), (15.03, 5000)])
    return etf, {"A": a, "B": b}


def test_plan_arb_directions_restricts_to_closing_side():
    etf, comps = _books(35.30, 35.32)                  # ETF rich: only SELL_ETF pays
    assert plan_arb(etf, comps, W, {}, "E", 0.0, 3000, 500, directions=("BUY_ETF",)) is None
    p = plan_arb(etf, comps, W, {}, "E", 0.0, 3000, 500, directions=("SELL_ETF",))
    assert p.direction == "SELL_ETF" and p.etf_qty <= 3000


def test_with_slippage_spends_only_the_surplus():
    p = ArbPlan("SELL_ETF", 1000, 0.13, 35.30, {"A": 20.01, "B": 15.01})
    w = with_slippage(p, W, entry_edge=0.10, share=1.0)
    assert w.etf_px < 35.30 and w.leg_px["A"] > 20.01  # wider limits on both sides
    spent = (35.30 - w.etf_px) + (w.leg_px["A"] - 20.01) + (w.leg_px["B"] - 15.01)
    assert abs(spent - 0.03) < 1e-9                    # never more than edge - entry_edge
    assert with_slippage(p, W, entry_edge=0.13, share=1.0) == p


def test_repair_completes_missing_etf_leg_when_premium_still_favours_it():
    pos = {"E": 0, "A": 1000, "B": 1000}               # bought basket, ETF sell missed
    assert repair_legs(pos, "E", W, premium=+0.20) == {"E": -1000}
    # Premium gone: unwind the basket instead of chasing the ETF.
    assert repair_legs(pos, "E", W, premium=-0.05) == {"A": -1000, "B": -1000}
    # Components disagree: square them to the ETF.
    assert repair_legs({"E": -1000, "A": 1000, "B": 400}, "E", W, premium=0.2) == {"B": 600}


# ------------------------------------------------------------------ liability
DEEP = make_book([(25.00, 20000), (24.98, 20000), (24.95, 20000)],
                 [(25.02, 20000), (25.04, 20000), (25.07, 20000)])


def test_tender_behind_existing_inventory_is_priced_at_the_margin():
    t = {"action": "BUY", "quantity": 20000, "price": 24.90, "is_fixed_bid": True}
    flat = evaluate_tender(t, DEEP, fee=0.02, refill_factor=1.0, price_queue=True)
    queued = evaluate_tender(t, DEEP, fee=0.02, position=40000, refill_factor=1.0, price_queue=True)
    ignored = evaluate_tender(t, DEEP, fee=0.02, position=40000, refill_factor=1.0, price_queue=False)
    assert queued.unwind_vwap < flat.unwind_vwap       # we sell into the book AFTER 40k already queued
    assert abs(ignored.unwind_vwap - flat.unwind_vwap) < 1e-9


# ------------------------------------------------------------------ equity
def test_end_of_period_skew_ramps_and_shifts_reservation():
    assert end_of_period_skew(200, 60, 3.0) == 1.0
    assert end_of_period_skew(30, 60, 3.0) == 2.5
    assert end_of_period_skew(0, 60, 3.0) == 4.0
    book = make_book([(25.00, 5000)], [(25.10, 5000)])
    p = QuoteParams(skew_per_share=0.00001)
    base = compute_quotes(book, 5000, 0.0, p)
    late = compute_quotes(book, 5000, 0.0, p, skew_mult=4.0)
    assert late.reservation < base.reservation        # long inventory: lean harder to sell
