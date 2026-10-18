from conftest import make_book
from ritc.strategies.liquidity import evaluate_tender, unwind_slice

# ------------------------------------------------------------------ liquidity
DEEP = make_book([(25.00, 20000), (24.98, 20000), (24.95, 20000)],
                 [(25.02, 20000), (25.04, 20000), (25.07, 20000)])


def test_accepts_cheap_buy_tender():
    d = evaluate_tender({"action": "BUY", "quantity": 20000, "price": 24.80, "is_fixed_bid": True},
                        DEEP, fee=0.02, min_profit=0.03)
    assert d.accept and d.profit_per_share > 0.1


def test_declines_rich_buy_tender():
    d = evaluate_tender({"action": "BUY", "quantity": 20000, "price": 25.00, "is_fixed_bid": True},
                        DEEP, fee=0.02, min_profit=0.03)
    assert not d.accept


def test_sell_tender_symmetry():
    d = evaluate_tender({"action": "SELL", "quantity": 20000, "price": 25.25, "is_fixed_bid": True},
                        DEEP, fee=0.02, min_profit=0.03)
    assert d.accept


def test_tender_respects_risk_room_and_time():
    t = {"action": "BUY", "quantity": 20000, "price": 24.0, "is_fixed_bid": True}
    assert not evaluate_tender(t, DEEP, 0.02, room=10000).accept
    assert not evaluate_tender(t, DEEP, 0.02, ticks_left=3).accept


def test_tender_size_matters():
    # Same price, but a block far bigger than the book -> slippage kills it.
    thin = make_book([(25.00, 1000), (24.80, 1000)], [(25.02, 1000)])
    t = {"action": "BUY", "quantity": 50000, "price": 24.90, "is_fixed_bid": True}
    assert not evaluate_tender(t, thin, 0.02, refill_factor=1.0).accept


def test_competitive_bid_keeps_margin():
    d = evaluate_tender({"action": "BUY", "quantity": 10000, "price": None, "is_fixed_bid": False},
                        DEEP, fee=0.02, competitive_margin=0.05, min_profit=0.03)
    assert d.accept and d.price < DEEP.best_bid
    assert abs(d.profit_per_share - 0.05) < 1e-9


def test_adverse_drift_penalises():
    t = {"action": "BUY", "quantity": 20000, "price": 24.90, "is_fixed_bid": True}
    calm = evaluate_tender(t, DEEP, 0.02)
    falling = evaluate_tender(t, DEEP, 0.02, drift_per_tick=-0.01, unwind_ticks_per_lot=0.001)
    assert falling.profit_per_share < calm.profit_per_share


def test_hold_valuation_prices_the_free_close_out_near_the_bell():
    # 2027 brief: positions left at the end close at the last traded price with no fine. Near the bell a
    # big block is worth ~mid (no fee, no book to walk), so a tender the book walk rejects is taken.
    thin = make_book([(25.00, 2000), (24.90, 2000)], [(25.02, 2000)])
    t = {"action": "BUY", "quantity": 30000, "price": 24.95, "is_fixed_bid": True}
    early = evaluate_tender(t, thin, 0.02, ticks_left=200, refill_factor=1.0, close_hold_ticks=90)
    late = evaluate_tender(t, thin, 0.02, ticks_left=60, refill_factor=1.0, close_hold_ticks=90)
    off = evaluate_tender(t, thin, 0.02, ticks_left=60, refill_factor=1.0, close_hold_ticks=0)
    assert not early.accept and not off.accept
    assert late.accept and abs(late.unwind_vwap - thin.mid) < 1e-9
    risky = evaluate_tender(t, thin, 0.02, ticks_left=60, refill_factor=1.0, close_hold_ticks=90,
                            price_vol_per_tick=0.05, risk_aversion=0.3)
    assert risky.profit_per_share < late.profit_per_share          # holding risk is still charged


def test_hold_risk_budget():
    from ritc.strategies.liquidity import hold_within_budget

    # 20k shares x $0.04/tick x sqrt(100 ticks) = $8k of 1-sd risk to the bell
    assert hold_within_budget(20_000, 0.04, 100, 10_000)
    assert not hold_within_budget(-20_000, 0.04, 100, 5_000)           # short side: same risk
    assert not hold_within_budget(20_000, 0.08, 100, 10_000)           # vol doubles -> cross the excess
    assert hold_within_budget(20_000, 0.08, 9, 10_000)                 # ...but not with 9 s to the bell
    assert not hold_within_budget(1_000, 0.0, 100, 10_000)             # no vol estimate yet: unwind as usual


def test_unwind_stuck():
    from ritc.strategies.liquidity import unwind_stuck

    fast = [(t, 55_000 - 3_000 * t) for t in range(11)]               # 3,000 shares/tick: flat in ~8 more
    slow = [(t, 55_000 - 800 * t) for t in range(11)]                 # 800/tick: 47k left needs ~59 ticks
    assert not unwind_stuck(fast, 10, 10, 30)
    assert unwind_stuck(slow, 10, 10, 30)
    assert not unwind_stuck(slow, 10, 10, 60)
    assert not unwind_stuck(slow[:5], 4, 10, 30)                       # too young to judge
    assert unwind_stuck([(t, 20_000) for t in range(11)], 10, 10, 30)  # nothing fills at all
    assert not unwind_stuck([(0, 5_000), (10, 0)], 10, 10, 30)         # done
    assert not unwind_stuck(slow, 10, 0, 30)                           # off


def test_passive_only_never_crosses():
    from ritc.core.algo import AlgoParams, plan_children

    p = AlgoParams(urgent_ticks=10)
    urgent = plan_children(DEEP, 30000, 0, 0, ticks_left=2, max_order=10000, p=p)
    assert urgent and not urgent[0].passive                        # normally: cross at the deadline
    held = plan_children(DEEP, 30000, 0, 0, ticks_left=2, max_order=10000, p=p, passive_only=True)
    assert len(held) == 1 and held[0].passive and held[0].action == "SELL" and held[0].price >= DEEP.best_ask


def test_unwind_slice():
    o = unwind_slice(DEEP, 30000, max_slippage=0.02, participation=0.5, max_order=10000)
    assert o[0] == "SELL" and 0 < o[1] <= 10000
    assert unwind_slice(DEEP, 0, 0.02, 0.5, 10000) is None
    assert unwind_slice(DEEP, -500, 0.02, 0.5, 10000)[0] == "BUY"


def test_auction_ignores_a_zero_or_absurd_reference_price():
    from ritc.core.book import Level, OrderBook
    # If the real API sends price=0 on a competitive tender, min(bid, 0) used to make us bid $0 (never filled).
    book = OrderBook("X", [Level(9.99, 50_000)], [Level(10.01, 50_000)])
    base = {"action": "BUY", "quantity": 10_000, "is_fixed_bid": False}
    none = evaluate_tender({**base, "price": None}, book, fee=0.02, min_profit=0.0)
    zero = evaluate_tender({**base, "price": 0}, book, fee=0.02, min_profit=0.0)
    silly = evaluate_tender({**base, "price": 2.50}, book, fee=0.02, min_profit=0.0)
    near = evaluate_tender({**base, "price": 9.80}, book, fee=0.02, min_profit=0.0)
    assert none.price == zero.price == silly.price > 9.5          # ignored
    assert near.price == 9.80                                      # a sane reference still caps the bid


def test_forward_liquidity_values_the_exit_at_our_resting_touch():
    """liquidity_model "flow": exit = the touch we rest at, unwind time = shares / forecast flow (practice heats:
    50k blocks went at ~2,500 shares/s near the touch, while walking the thin visible book priced them far worse)."""
    from ritc.strategies.liquidity import evaluate_tender
    thin = make_book([(9.99, 1000), (9.98, 1000), (9.90, 1000)], [(10.01, 1000), (10.02, 1000), (10.10, 1000)])
    t = {"action": "BUY", "quantity": 50_000, "price": 9.80, "is_fixed_bid": True}
    walk = evaluate_tender(t, thin, fee=0.02, min_profit=0.01)
    flow = evaluate_tender(t, thin, fee=0.02, min_profit=0.01, flow_rate=2500)
    assert flow.unwind_vwap == 10.01 and flow.accept and flow.profit_per_share > walk.profit_per_share
    # Less liquidity -> longer unwind -> bigger risk premium for the same tender.
    slow = evaluate_tender(t, thin, fee=0.02, min_profit=0.01, flow_rate=500, price_vol_per_tick=0.04,
                           risk_aversion=0.3)
    fast = evaluate_tender(t, thin, fee=0.02, min_profit=0.01, flow_rate=5000, price_vol_per_tick=0.04,
                           risk_aversion=0.3)
    assert slow.profit_per_share < fast.profit_per_share


def test_adaptive_liquidity_switches_only_without_a_crowd():
    from ritc.strategies.liquidity import BayesImpact, LiquidityStrategy

    s = LiquidityStrategy.__new__(LiquidityStrategy)
    s.p, s.crowd_learn = {"adaptive_min_obs": 2}, True
    s.crowd = BayesImpact(0.0, 0.10, 0.10)
    assert not s.no_crowd_seen()                        # nothing scored yet: price with the book walk
    s.crowd.update(1.0, 0.0)
    s.crowd.update(1.0, 0.01)
    assert s.no_crowd_seen()                            # two quiet tenders: forward liquidity
    for _ in range(3):
        s.crowd.update(1.0, 0.20)
    assert not s.no_crowd_seen()                        # a crowd shows: back to the book walk


def test_ensemble_weights_follow_the_crowd_posterior():
    from ritc.strategies.liquidity import BayesImpact, LiquidityStrategy

    s = LiquidityStrategy.__new__(LiquidityStrategy)
    s.p, s.crowd_learn = {"ensemble_crowd_prior": 0.15, "ensemble_flow_weight": 0.5}, True
    s.crowd = BayesImpact(0.0, 0.10, 0.10)
    w = s.scenario_weights()
    assert abs(sum(w.values()) - 1) < 1e-9 and abs(w["crowd"] - 0.15) < 1e-9 and w["book"] == w["flow"]
    for _ in range(4):
        s.crowd.update(5.0, 0.5)                       # a clear crowd: 0.10 $/10k
    assert s.scenario_weights()["crowd"] > 0.9


def test_trend_against_is_a_z_score_of_adverse_drift():
    from types import SimpleNamespace

    from ritc.strategies.liquidity import LiquidityStrategy

    s = LiquidityStrategy.__new__(LiquidityStrategy)
    s.p = {"drift_halflife": 15}
    s.drift = {"X": SimpleNamespace(mean=-0.01)}       # falling 1 cent a tick
    s.price_vol = lambda t, snap: 0.04
    z = s.trend_against("X", 50_000, None)             # long into a falling market: against us
    assert abs(z - 0.01 / (0.04 / (2.9 * 15) ** 0.5)) < 1e-9 and z > 1.5
    assert s.trend_against("X", -50_000, None) < 0     # short: the fall is in our favour
    assert s.trend_against("X", 0, None) == 0.0


def test_drift_zero_start_does_not_let_the_first_tick_set_the_drift():
    from ritc.strategies.liquidity import LiquidityStrategy

    s = LiquidityStrategy.__new__(LiquidityStrategy)
    s.p = {"drift_halflife": 15}
    old = s.new_drift()
    old.update(-0.05)                                  # one 5-cent tick
    assert old.mean == -0.05                           # default: the first tick IS the drift
    s.p = {"drift_halflife": 15, "drift_zero_start": True}
    new = s.new_drift()
    new.update(-0.05)
    assert abs(new.mean - new.alpha * -0.05) < 1e-12   # zero start: it gets only its EWMA weight (~4.5%)


def test_flow_guarded_prices_off_flow_until_the_crowd_alarm():
    from ritc.strategies.liquidity import BayesImpact, LiquidityStrategy

    s = LiquidityStrategy.__new__(LiquidityStrategy)
    s.p = {"crowd_gate": True}
    s.crowd_learn = True
    s.crowd = BayesImpact(0.0, 0.10, 0.10)
    t = {"action": "BUY", "quantity": 50_000, "is_fixed_bid": True}
    s.liq_model = "adaptive"
    assert not s.flow_pricing(t, {})                  # adaptive: book until evidence of NO crowd
    s.liq_model = "flow_guarded"
    assert s.flow_pricing(t, {})                      # flow_guarded: flow from the start of the heat...
    s.crowd = BayesImpact(0.05, 0.01, 0.10)
    s.crowd.n = 5
    assert s.crowded() and not s.flow_pricing(t, {})  # ...and the cautious book walk once the crowd alarm is on


def test_auction_skip_adding_declines_auctions_that_grow_an_unwinding_block():
    from types import SimpleNamespace

    from ritc.strategies.liquidity import LiquidityStrategy

    s = LiquidityStrategy.__new__(LiquidityStrategy)
    s.p, s.liq_model = {"auction_skip_adding": 5000}, "book"
    s._evaluate = lambda t, snap, positions: SimpleNamespace(accept=True, reason="evaluated")
    sell_auction = {"ticker": "CROC", "action": "SELL", "quantity": 54000, "is_fixed_bid": False}
    assert not s.decide(sell_auction, None, {"CROC": -35000}).accept        # short CROC, SELL auction: adds
    assert s.decide(sell_auction, None, {"CROC": 35000}).accept             # long: it would offset
    assert s.decide(sell_auction, None, {"CROC": -2000}).accept             # under the threshold
    assert s.decide(dict(sell_auction, is_fixed_bid=True), None, {"CROC": -35000}).accept   # fixed tenders unaffected


def test_netting_credit_values_the_offset_of_an_open_block():
    # We are short 30k: a 30k auction to BUY closes it, sparing the unwind its expected cost.
    t = {"action": "BUY", "quantity": 30000, "price": None, "is_fixed_bid": False}
    plain = evaluate_tender(t, DEEP, 0.02, position=-30000)
    netted = evaluate_tender(t, DEEP, 0.02, position=-30000, offset_credit=0.20)
    assert netted.price == round(plain.price + 0.20, 2)
    # Nothing to offset: the credit changes nothing.
    assert evaluate_tender(t, DEEP, 0.02, offset_credit=0.20).price == evaluate_tender(t, DEEP, 0.02).price
