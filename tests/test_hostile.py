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
