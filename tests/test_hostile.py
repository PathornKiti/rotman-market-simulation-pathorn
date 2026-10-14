"""Hostile market mode of the simulator, and the liability bot's crowd learning."""

from ritc.core.book import Level, OrderBook
from ritc.core.execution import Executor
from ritc.sim.server import Episode, Market
from ritc.strategies.liability import evaluate_tender


def active(case: str, seed: int = 1, hostile: float = 0.0) -> Market:
    m = Market(case, 300, 2 if case == "derivatives" else 1, seed=seed, hostile=hostile)
    m.status = "ACTIVE"
    return m


def mids(m: Market, n: int) -> list[float]:
    out = []
    for _ in range(n):
        m.advance()
        out.append(round(sum(s.mid for s in m.secs.values()), 9))
    return out


# ------------------------------------------------------------------ simulator
def test_hostile_zero_is_the_benign_market():
    for case in ("equity", "liability", "etf", "derivatives"):
        assert mids(active(case, 4), 120) == mids(active(case, 4, hostile=0.0), 120)


def test_hostile_market_is_reproducible_and_different():
    a, b = mids(active("equity", 5, 1.0), 200), mids(active("equity", 5, 1.0), 200)
    assert a == b
    assert a != mids(active("equity", 5), 200)


def test_episode_ramps_holds_and_reverts():
    e = Episode(start=10, amp=0.5, up=5, hold=2, down=4, residual=0.5)
    assert e.offset(10) == 0.0 and e.offset(15) == 0.5 and e.offset(17) == 0.5
    assert abs(e.offset(21) - 0.25) < 1e-12 and e.done(22) and not e.done(21)


def test_spoof_level_is_visible_but_never_fills():
    m = active("equity", 1, hostile=1.0)
    m.advance()
    m.spoofs["SPNG"] = ("SELL", m.abs_tick + 5, 50_000)
    m._hostile_books()
    s = m.secs["SPNG"]
    assert any(len(lv) > 2 for lv in s.asks)
    book = m.book_view(s, 20)
    assert max(r["quantity"] for r in book["ask"]) == 50_000            # shown in the book...
    before = sum(lv[1] for lv in s.asks if len(lv) > 2)
    m.submit("SPNG", "MARKET", 10_000, "BUY", None)
    assert sum(lv[1] for lv in s.asks if len(lv) > 2) == before          # ...but nothing traded against it


def test_vacuum_pulls_the_touch_but_not_the_mid():
    m = active("liability", 1, hostile=1.0)
    m.advance()
    s = m.secs["CRZY"]
    mid, spread = s.mid, s.asks[0][0] - s.bids[0][0]
    m.vacuums["CRZY"] = m.abs_tick + 3
    m._hostile_books()
    assert s.mid == mid
    assert s.asks[0][0] - s.bids[0][0] >= 3 * spread


def test_penny_jumper_takes_our_queue_but_not_through_fair():
    m = active("equity", 1, hostile=100.0)                 # jump probability capped at 1
    m.advance()
    s = m.secs["SPNG"]
    s.bids = [[round(s.mid - 0.05, 2), 1000]] + s.bids[1:]
    o = m.submit("SPNG", "LIMIT", 1000, "BUY", s.bids[0][0])            # we join the touch
    m._hostile_books()
    assert s.bids[0][0] > o["price"] and s.bids[0][0] <= s.mid - s.tick  # someone stepped in front
    # A quote already at fair minus one tick can't be jumped by a competitor that keeps its edge.
    m2 = active("equity", 1, hostile=100.0)
    m2.advance()
    s2 = m2.secs["SPNG"]
    px = round(int((s2.mid - 0.01) * 100) / 100, 2)
    m2.submit("SPNG", "LIMIT", 1000, "BUY", px)
    m2._hostile_books()
    assert max(lv[0] for lv in s2.bids) <= px                           # nobody stepped in front


def test_crowded_tender_moves_price_against_the_unwind():
    m = active("liability", 2, hostile=1.0)
    m.threats = {"crowd"}
    while not m.tenders:
        m.advance()
    tender = next(iter(m.tenders.values()))
    t, before = tender["ticker"], m.secs[tender["ticker"]].mid - m.adv_off.get(tender["ticker"], 0.0)
    for _ in range(12):
        m.advance()
    sign = -1 if tender["action"] == "BUY" else 1
    assert sign * m.adv_off[t] > 0                                      # the crowd pushed against our unwind
    assert before                                                       # (true price kept separately)


# ------------------------------------------------------------------ liability
def test_crowd_cost_lowers_tender_profit():
    book = OrderBook("CRZY", [Level(25.00, 50_000)], [Level(25.02, 50_000)])
    t = {"action": "BUY", "quantity": 20_000, "price": 24.80, "is_fixed_bid": True}
    clean = evaluate_tender(t, book, fee=0.02, min_profit=0.01)
    crowded = evaluate_tender(t, book, fee=0.02, min_profit=0.01, crowd_cost=0.20)
    assert clean.accept and not crowded.accept
    assert abs(clean.profit_per_share - crowded.profit_per_share - 0.20) < 1e-9


def test_liability_learns_the_crowd_and_races_it():
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    cfg = {"case": {"tickers": ["X"]},
           "strategy": {"crowd_learn": True, "crowd_prior_mean": 0.0, "crowd_ticks": 2},
           "execution": {"unwind_horizon_ticks": 30, "crowd_horizon_ticks": 12}}
    s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))
    assert s.crowd_cost(30_000) == 0.0 and s.unwind_horizon() == 30

    def snap(tick: int, mid: float) -> Snapshot:
        return Snapshot({"tick": tick, "period": 1, "ticks_per_period": 300},
                        {"X": {"bid": mid - 0.01, "ask": mid + 0.01}})

    for k in range(6):          # every 20k BUY tender (we must sell) is followed by a 12-cent drop
        s.crowd_obs.append((10 * k, "X", -1, 2.0, 25.0, k))
        s._learn_crowd(snap(10 * k + 2, 24.88))
    assert 0.05 < s.crowd.mean < 0.06 and s.crowded() and s.unwind_horizon() == 12
    assert s.crowd_cost(20_000) > 0.1


def test_crowd_gate_charges_nothing_without_evidence_and_skips_our_own_tenders():
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    cfg = {"case": {"tickers": ["X"]},
           "strategy": {"crowd_learn": True, "crowd_gate": True, "crowd_untaken_only": True, "crowd_ticks": 2}}
    s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))
    snap = Snapshot({"tick": 12, "period": 1, "ticks_per_period": 300}, {"X": {"bid": 24.87, "ask": 24.89}})
    s.taken = {1}
    s.crowd_obs = [(10, "X", -1, 2.0, 25.0, 1)]          # a tender we took: its drop is our own selling
    s._learn_crowd(snap)
    assert s.crowd.n == 0 and s.crowd_cost(50_000) == 0.0
    s.crowd_obs = [(10, "X", -1, 2.0, 25.0, 2)]          # one we declined: a real crowd signal...
    s._learn_crowd(snap)
    assert s.crowd.n == 1
    assert s.crowded() == (s.crowd.mean > 2 * s.crowd.sd)  # ...charged only once it is significant
    assert (s.crowd_cost(50_000) > 0) == s.crowded()


def test_rejected_auction_bid_still_freezes_the_stock_until_expiry():
    # 2027 brief: trading during a tender's window "before the decision is finalized" is front-running.
    # An auction is only final at expiry, even when the server answers success=false at once.
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    tender = {"tender_id": 7, "ticker": "X", "action": "BUY", "quantity": 10_000, "price": None,
              "is_fixed_bid": False, "tick": 10, "expires": 40}

    class Client:
        def tenders(self):
            return [tender]

        def accept_tender(self, tid, price=None):
            return {"success": False}

    for freeze in (True, False):
        cfg = {"case": {"tickers": ["X"], "fee": {"X": 0.02}},
               "strategy": {"freeze_rejected_bids": freeze, "respect_windows": True, "min_profit_per_share": -1}}
        s = LiabilityStrategy(Context(Client(), Executor(None, dry_run=False), RiskManager(), cfg))
        book = OrderBook("X", [Level(24.99, 50_000)], [Level(25.01, 50_000)])
        snap = Snapshot({"tick": 10, "period": 1, "ticks_per_period": 300}, {"X": {"bid": 24.99, "ask": 25.01}})
        snap._books["X"] = book
        s.handle_tenders(snap)
        tender_gone = Snapshot({"tick": 20, "period": 1, "ticks_per_period": 300}, {"X": {}})
        s.current_tenders = lambda: []
        assert (s.undecided(tender_gone) == {"X"}) == freeze
        assert s.undecided(Snapshot({"tick": 41, "period": 1, "ticks_per_period": 300}, {"X": {}})) == set()


def test_anchor_prices_tenders_off_the_visible_mid(monkeypatch):
    # RITC_STRESS anchor=1: tenders are quoted off the VISIBLE mid (true + crowd / manipulation offset), as a real
    # server pricing off the current market would; default: off the true mid. Same tender stream, same market.
    def run(anchor: str) -> list[tuple]:
        monkeypatch.setenv("RITC_STRESS", f"anchor={anchor}")
        m = Market("liability", 420, 1, seed=7, hostile=1.0)
        m.status = "ACTIVE"
        out, seen = [], set()
        for _ in range(250):
            off = dict(m.adv_off)                      # the offset the new tenders are priced against
            m.advance()
            for tid, t in m.tenders.items():
                if tid not in seen:
                    seen.add(tid)
                    out.append((tid, t["ticker"], t["action"], t["quantity"], t["price"], t["_reserve"],
                                off.get(t["ticker"], 0.0)))
        return out

    true, vis = run("0"), run("1")
    assert [x[:4] for x in true] == [x[:4] for x in vis] and len(true) > 5
    shifted = 0
    for a, b in zip(true, vis):
        assert abs((b[5] - a[5]) - b[6]) < 0.011             # reserve moved by the visible offset
        if a[4] is not None:
            assert abs((b[4] - a[4]) - b[6]) < 0.011         # so did the fixed price
        shifted += abs(b[6]) > 0.05
    assert shifted                                            # and the offset was material for some tenders


def test_decide_late_waits_for_the_deadline_but_never_risks_missing_a_tender():
    # decide_late_ticks: answer a tender `late` ticks before it expires (option value: re-price then).
    # Safety: answer at once if `expires` is missing or the window looks wrong, and never wait past the cap.
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    cfg = {"case": {"tickers": ["X"]}, "strategy": {"decide_late_ticks": 3, "decide_late_max_window": 30}}
    s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))

    def snap(tick: int) -> Snapshot:
        return Snapshot({"tick": tick, "period": 1, "ticks_per_period": 420}, {"X": {"bid": 9.99, "ask": 10.01}})

    t = {"tender_id": 1, "ticker": "X", "tick": 100, "expires": 125}
    assert s.wait_to_decide(t, 1, snap(100))               # 25 ticks left: wait
    assert s.wait_to_decide(t, 1, snap(121))               # 4 left: still wait
    assert not s.wait_to_decide(t, 1, snap(122))           # 3 left: decide now
    assert not s.wait_to_decide({"tender_id": 2, "ticker": "X"}, 2, snap(100))           # no expires
    assert not s.wait_to_decide({"tender_id": 3, "expires": "?"}, 3, snap(100))          # unreadable
    assert not s.wait_to_decide({"tender_id": 4, "expires": 400}, 4, snap(100))          # 300 away: wrong unit?
    s.first_seen[5] = 70                                                                   # waited 30 already
    assert not s.wait_to_decide({"tender_id": 5, "expires": 125}, 5, snap(100))
    # A window that runs past the bell: answer while the tender can still be accepted (min_ticks 5, wind-down 5).
    end = LiabilityStrategy(Context(None, Executor(None), RiskManager(),
                                    {"case": {"tickers": ["X"]}, "run": {"wind_down_ticks": 5},
                                     "strategy": {"decide_late_ticks": 3, "min_ticks_to_unwind": 5}}))
    assert end.wait_to_decide({"tender_id": 6, "expires": 421}, 6, snap(400))     # 20 left in the heat: wait
    assert not end.wait_to_decide({"tender_id": 6, "expires": 421}, 6, snap(411))  # 9 left: answer now
    off = LiabilityStrategy(Context(None, Executor(None), RiskManager(), {"case": {"tickers": ["X"]}}))
    assert not off.wait_to_decide(t, 1, snap(100))         # off by default


def test_room_wait_rechecks_a_tender_declined_only_for_room():
    # room_wait: a fixed tender blocked only by risk room is re-checked until `room_wait_margin` ticks before it
    # expires (the room is held by a block unwinding elsewhere or an auction bid that may lose), but never when
    # we hold its own stock (waiting freezes that stock's unwind), never for auctions, never past the last tick.
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy, TenderDecision

    cfg = {"case": {"tickers": ["X", "Y"]}, "run": {"wind_down_ticks": 5},
           "strategy": {"room_wait": True, "min_ticks_to_unwind": 5}}
    s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))

    def snap(tick: int, x_pos: int = 0) -> Snapshot:
        return Snapshot({"tick": tick, "period": 1, "ticks_per_period": 420},
                        {"X": {"bid": 9.99, "ask": 10.01, "position": x_pos}, "Y": {"position": 55_000}})

    room = TenderDecision(False, None, 0.0, 0.0, "exceeds risk room (53000 > 44000)")
    t = {"tender_id": 103, "ticker": "X", "expires": 130, "is_fixed_bid": True}
    assert s.wait_for_room(t, room, snap(100))                 # Y's block is unwinding: keep the option open
    assert s.wait_for_room(t, room, snap(127))                 # 3 left
    assert not s.wait_for_room(t, room, snap(128))             # 2 left: answer now (decline)
    assert not s.wait_for_room(t, room, snap(100, x_pos=20_000))      # we hold X: waiting would freeze its unwind
    assert not s.wait_for_room(t, TenderDecision(False, None, -0.2, 0.0, "profit/share -0.2000 < 0.0100"),
                               snap(100))                      # declined on price: no reason to wait
    assert not s.wait_for_room({**t, "is_fixed_bid": False}, room, snap(100))   # auctions: bid or not, at once
    assert not s.wait_for_room({**t, "expires": 425}, room, snap(414))          # 6 left: past the last acceptable tick
    off = LiabilityStrategy(Context(None, Executor(None), RiskManager(), {"case": {"tickers": ["X"]}}))
    assert not off.wait_for_room(t, room, snap(100))           # off unless configured


def test_holder_drift_switches_on_only_when_the_price_runs_against_what_we_hold():
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    cfg = {"case": {"tickers": ["X"]},
           "strategy": {"holder_drift_learn": True, "holder_min_ticks": 20, "holder_z": 2.5,
                        "holder_horizon_ticks": 20},
           "execution": {"unwind_horizon_ticks": 30}}

    def run(moves: list[float], pos: int) -> LiabilityStrategy:
        s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))
        mid = 25.0
        for k, dm in enumerate(moves):
            mid += dm
            s._update_drift(Snapshot({"tick": k + 1, "period": 1, "ticks_per_period": 300},
                                     {"X": {"bid": mid - 0.01, "ask": mid + 0.01, "position": pos}}))
        return s

    # Long 50k while the mid falls ~0.7c a tick with noise: learned, and the bot races its unwinds.
    falling = run([-0.007 + (0.01 if k % 2 else -0.01) for k in range(60)], 50_000)
    assert 0.005 < falling.holder_drift() < 0.009 and falling.unwind_horizon() == 20
    assert falling.holder_cost("X", 50_000) > 0.05
    # The same moves while flat, or a random-walk-like zigzag while long: nothing.
    assert run([-0.007 + (0.01 if k % 2 else -0.01) for k in range(60)], 0).holder_drift() == 0.0
    assert run([0.01 if k % 2 else -0.01 for k in range(60)], 50_000).holder_drift() == 0.0


def test_hold_test_freezes_a_fresh_block_then_logs_the_move_against_it():
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    cfg = {"case": {"tickers": ["X"]}, "execution": {"hold_test_ticks": 30, "hold_test_max": 1}}
    s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))

    def snap(tick: int, mid: float, pos: int) -> Snapshot:
        return Snapshot({"tick": tick, "period": 1, "ticks_per_period": 420},
                        {"X": {"bid": mid - 0.01, "ask": mid + 0.01, "position": pos}})

    book = OrderBook("X", [Level(24.99, 1000)], [Level(25.01, 1000)])
    s.maybe_hold_test("X", 1, 50_000, True, book, snap(10, 25.0, 0), 10)
    assert s.frozen_now(snap(20, 24.9, 50_000)) == {"X"}            # untouched while the test runs
    assert s.frozen_now(snap(40, 24.8, 50_000)) == set()            # over: unwind as usual
    assert s.hold_test_log[0]["against"] == 0.2 and s.hold_test_log[0]["ticks"] == 30
    s.maybe_hold_test("X", 1, 50_000, True, book, snap(50, 25.0, 0), 50)
    assert not s.hold_tests                                         # at most hold_test_max per heat


def test_trend_learner_starts_at_the_prior_and_learns_this_heats_momentum():
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    cfg = {"case": {"tickers": ["X"]},
           "strategy": {"trend_learn": True, "trend_value": True, "trend_prior_r": 0.46, "trend_prior_n": 20}}

    def run(moves: list[float]) -> LiabilityStrategy:
        s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))
        mid = 25.0
        for k, dm in enumerate(moves):
            mid += dm
            s._update_drift(Snapshot({"tick": k + 1, "period": 1, "ticks_per_period": 2000},
                                     {"X": {"bid": mid - 0.01, "ask": mid + 0.01}}))
        return s

    assert abs(run([]).trend_r() - 0.46) < 1e-9               # nothing seen yet: the measured prior
    # 10-tick runs that alternate direction: momentum is negative, so the learned r falls toward 0
    zigzag = run([0.01 if (k // 10) % 2 else -0.01 for k in range(1200)])
    assert zigzag.trend_r() < 0.05
    # persistent runs of 40 ticks: positive momentum, stays high
    runs = run([0.01 if (k // 40) % 2 else -0.01 for k in range(1200)])
    assert runs.trend_r() > 0.4
    # the last 10 ticks rose: against a short (+), for a long (-); the valuation charges / credits it
    assert runs.run_against("X", -1) > 0 > runs.run_against("X", 1)
    assert runs.trend_cost("X", -1) > 0 and runs.trend_cost("X", 1) == -runs.trend_cost("X", -1)


def test_sprint_crosses_the_whole_block_at_once_within_participation():
    from conftest import make_book
    from ritc.core.algo import AlgoParams, BlockExecutor

    book = make_book([(24.99, 20000), (24.98, 20000)], [(25.01, 20000), (25.02, 20000)])
    algo = BlockExecutor(Executor(None), AlgoParams(urgent_participation=0.35))
    algo.work("X", 50_000, 0, now=0, deadline=60)
    algo.passive = {"X"}
    assert all(c.passive for c in algo._kids("X", algo.blocks["X"], book, 50_000, 1))
    algo.sprint = {"X"}
    kids = algo._kids("X", algo.blocks["X"], book, 50_000, 1)
    assert any(not c.passive and c.action == "SELL" and 0 < c.qty <= 0.35 * 40_000 for c in kids)


def test_book_signal_reads_the_thin_side_and_learns_its_weight():
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy, cross_cost

    thin_asks = OrderBook("X", [Level(24.99, 30000)], [Level(25.01, 5000), Level(25.10, 30000)])
    assert abs(cross_cost(thin_asks, "SELL", 25000) - 0.01) < 1e-9          # bids absorb 25k at the touch
    assert cross_cost(thin_asks, "BUY", 25000) > 0.07                       # asks: 5k at the touch, the rest 9c out
    assert cross_cost(thin_asks, "BUY", 100000) == 0.30                     # cannot fill: capped

    cfg = {"case": {"tickers": ["X"]}, "strategy": {"book_signal": True, "book_prior_n": 20}}
    s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))
    a, b = s.book_beta()
    assert abs(a - 0.40) < 1e-9 and abs(b - 0.67) < 1e-9                    # the real fit until data arrives
    snap = Snapshot({"tick": 1, "period": 1, "ticks_per_period": 420}, {"X": {"bid": 24.99, "ask": 25.01}})
    snap._books["X"] = thin_asks
    s.mid_hist["X"] = [25.0] * 11
    # asks thin -> the price should rise: against a short, for a long
    assert s.predicted_against("X", -50_000, snap) > 0 > s.predicted_against("X", 50_000, snap)
    # windows where the asymmetry never predicted anything: its weight shrinks toward 0
    s.sig_xx[1][1], s.sig_n = 400 * 0.00631, 400
    assert s.book_beta()[1] < 0.05


def test_start_sprint_races_only_when_a_crowd_is_detected():
    from conftest import make_book
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    cfg = {"case": {"tickers": ["X"]}, "strategy": {"trend_exec": True, "crowd_learn": True},
           "execution": {"initial_sprint_ticks": 3, "initial_sprint_gate": "crowded", "hold_risk_budget": 0}}
    s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))
    s.tickers = ["X"]
    s.ex.dry_run = True
    snap = Snapshot({"tick": 5, "period": 1, "ticks_per_period": 420},
                    {"X": {"bid": 24.99, "ask": 25.01, "position": 50_000}})
    snap._books["X"] = make_book([(24.99, 20000)], [(25.01, 20000)])
    s.unwind_block(snap)
    assert "X" in s.algo.blocks and "X" not in s.algo.sprint          # no crowd seen: rest as usual
    s.crowded = lambda: True
    s.unwind_block(snap)
    assert "X" in s.algo.sprint                                       # crowd: cross the block's first ticks


def test_sprint_never_walks_the_price_past_its_cap():
    # Practice 2026-10-14: a CROC sprint bought 59.5k from 20.29 up to 21.54 in 8 s, re-anchoring its limit on
    # the touch it had just moved every loop. With sprint_max_walk it pays at most that far from its start mid.
    from conftest import make_book
    from ritc.core.bot import Context, Snapshot
    from ritc.core.risk import RiskManager
    from ritc.strategies.liability import LiabilityStrategy

    cfg = {"case": {"tickers": ["X"]}, "strategy": {"trend_exec": True},
           "execution": {"initial_sprint_ticks": 3, "initial_sprint_gate": "always", "hold_risk_budget": 0,
                         "sprint_max_walk": 0.10}}
    s = LiabilityStrategy(Context(None, Executor(None), RiskManager(), cfg))
    s.tickers = ["X"]
    s.ex.dry_run = True

    def at(bid, ask, tick):
        snap = Snapshot({"tick": tick, "period": 1, "ticks_per_period": 420},
                        {"X": {"bid": bid, "ask": ask, "position": -50_000}})
        snap._books["X"] = make_book([(bid, 20000)], [(ask, 2000), (ask + 0.30, 50000)])
        return snap

    s.unwind_block(at(19.99, 20.01, 5))                 # short block: the sprint buys, start mid 20.00
    assert "X" in s.algo.sprint and s.algo.sprint_limit["X"] == 20.10
    b = s.algo.blocks["X"]
    kids = s.algo._kids("X", b, make_book([(19.99, 20000)], [(20.01, 2000), (20.31, 50000)]), -50_000, 5)
    assert all(c.price <= 20.10 for c in kids if not c.passive)          # never reaches the 20.31 level
    s.unwind_block(at(20.14, 20.16, 6))                 # our buying moved the touch 0.16: stop crossing, rest
    assert "X" not in s.algo.sprint
    s.unwind_block(at(20.03, 20.05, 7))                 # back inside the cap: the same anchor, sprint again
    assert "X" in s.algo.sprint and s.algo.sprint_limit["X"] == 20.10


def test_calibrate_reads_market_rows_from_a_bot_journal(tmp_path):
    # `ritc run --record` writes the recorder's snapshots into the bot journal as "market" rows
    import json

    from ritc.recorder import load_snapshots

    snap = {"t": 1.0, "case": {"tick": 5, "status": "ACTIVE"}, "securities": [], "books": {}}
    p = tmp_path / "liability-x.jsonl"
    p.write_text("\n".join([json.dumps({"k": "start", "w": 0}), json.dumps({"k": "market", "w": 1, **snap}),
                            json.dumps({"k": "tick", "w": 2, "tick": 5}), '{"k": "market", "w": 3, "ca']) + "\n")
    assert load_snapshots(str(p)) == [snap]                   # other kinds and a cut-off last line skipped
    old = tmp_path / "record-x.jsonl"                          # a plain `ritc record` file still loads
    old.write_text(json.dumps(snap) + "\n")
    assert load_snapshots(str(old)) == [snap]
