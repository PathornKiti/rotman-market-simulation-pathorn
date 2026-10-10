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
