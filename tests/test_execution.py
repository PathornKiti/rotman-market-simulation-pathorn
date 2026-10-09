import math
import socket
import time

import pytest

from conftest import make_book
from ritc.core.algo import AlgoParams, plan_children, schedule_position
from ritc.core.client import RITClient
from ritc.core.feed import EventFeed
from ritc.core.risk import DrawdownGuard
from ritc.core.tca import TCA
from ritc.sim.server import serve
from ritc.tune import Result, combinations, parse_grid, wins

BOOK = make_book([(25.00, 5000), (24.99, 5000), (24.95, 10000)],
                 [(25.04, 5000), (25.05, 5000), (25.10, 10000)])
P = AlgoParams(max_slippage=0.03, participation=0.5, display_qty=4000, urgent_ticks=5)


# ------------------------------------------------------------------ schedule
def test_schedule_linear_and_front_loaded():
    assert schedule_position(10000, 0, 0, 100, 0) == 10000
    assert schedule_position(10000, 0, 0, 100, 50) == 5000
    assert schedule_position(10000, 0, 0, 100, 100) == 0
    assert schedule_position(10000, 0, 0, 100, 500) == 0
    assert schedule_position(10000, 0, 0, 100, 50, front_load=0.5) < 5000   # more done early


# ---------------------------------------------------------- child orders
def test_on_schedule_posts_passive_iceberg_only():
    kids = plan_children(BOOK, 20000, sched=20000, target=0, ticks_left=50, max_order=10000, p=P)
    assert len(kids) == 1 and kids[0].passive
    assert kids[0].action == "SELL" and kids[0].qty == 4000          # iceberg display
    assert kids[0].price == pytest.approx(25.03)                      # one tick inside a 4-tick spread


def test_behind_schedule_crosses_for_the_shortfall():
    kids = plan_children(BOOK, 20000, sched=12000, target=0, ticks_left=50, max_order=10000, p=P)
    agg = [k for k in kids if not k.passive]
    assert agg and agg[0].action == "SELL" and 0 < agg[0].qty <= 8000
    assert agg[0].price == pytest.approx(24.97)                       # touch - max_slippage
    assert any(k.passive for k in kids)


def test_urgent_crosses_everything():
    kids = plan_children(BOOK, 20000, sched=15000, target=0, ticks_left=3, max_order=10000, p=P)
    assert len(kids) == 1 and not kids[0].passive and kids[0].qty == 10000


def test_limit_price_is_respected():
    kids = plan_children(BOOK, 20000, sched=10000, target=0, ticks_left=50, max_order=10000, p=P,
                         limit_price=25.035)
    for k in kids:
        assert k.price >= 25.035                                      # never sell below the limit


def test_buy_side_and_done():
    kids = plan_children(BOOK, -8000, sched=-8000, target=0, ticks_left=50, max_order=10000, p=P)
    assert kids[0].action == "BUY" and kids[0].price == pytest.approx(25.01)
    assert plan_children(BOOK, 0, 0, 0, 50, 10000, P) == []


# ----------------------------------------------------------- TCA / guard
def test_tca_shortfall_signs():
    t = TCA()
    t.record_sent("X", 100, "aggressive")
    t.record_fill("X", "BUY", 100, 10.02, 10.00, "aggressive")       # paid 2c
    t.record_fill("X", "SELL", 100, 10.03, 10.00, "passive")         # earned 3c
    s = t.summary()
    assert s[("X", "aggressive")].per_unit == pytest.approx(0.02)
    assert s[("X", "passive")].per_unit == pytest.approx(-0.03)
    assert t.total_cost() == pytest.approx(-1.0)
    assert "TOTAL" in t.report()


def test_drawdown_guard():
    g = DrawdownGuard(1000)
    assert not g.update(0) and not g.update(5000) and not g.update(4200)
    assert g.update(3900) and g.tripped
    assert not g.update(10000) and g.tripped                          # stays tripped
    assert not DrawdownGuard(0).update(-1e9)                          # disabled


# ----------------------------------------------------------------- tuning
def test_parse_grid_and_combinations():
    g = parse_grid(["strategy.a=1,2", "execution.mode=block,slice", "run.flag=true"])
    assert g == {"strategy.a": [1, 2], "execution.mode": ["block", "slice"], "run.flag": [True]}
    assert len(combinations(g)) == 4 and combinations({}) == [{}]
    with pytest.raises(ValueError):
        parse_grid(["nonsense"])


def test_wins_counts_best_per_seed():
    rs = [Result({"a": 1}, {1: 10.0, 2: 5.0}), Result({"a": 2}, {1: 8.0, 2: 9.0})]
    assert wins(rs) == {0: 1, 1: 1}


# --------------------------------------------------------- speed / realtime
def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_parallel_calls_and_feed_against_sim():
    port = _port()
    srv, m = serve("commodity", port, speed=50, delay=0.0, seed=1, block=False)
    try:
        c = RITClient(base_url=f"http://127.0.0.1:{port}/v1")
        res = c.parallel([c.case, c.trader, lambda: c.book("CL"), lambda: 1 / 0])
        assert res[0]["name"] == "SIM-COMMODITY" and "nlv" in res[1] and "bid" in res[2]
        assert isinstance(res[3], ZeroDivisionError)                  # errors returned, not raised

        feed = EventFeed(c, poll=0.02, news=True).start()
        deadline = time.time() + 5
        while m.abs_tick < 21 and time.time() < deadline:            # first inventory report at tick 20
            time.sleep(0.05)
        assert feed.wait(2.0)
        news = feed.drain_news()
        assert news and "inventor" in news[0]["body"].lower()
        assert feed.drain_news() == []                                # drained exactly once
        feed.stop()
    finally:
        srv.shutdown()


def test_paired_stats_against_baseline():
    from ritc.tune import paired, report
    base = Result({}, {1: 100.0, 2: 200.0, 3: 300.0})
    better = Result({"x": 1}, {1: 110.0, 2: 215.0, 3: 305.0})
    d, se, t, beat, n = paired(better, base)
    assert (round(d, 6), beat, n) == (10.0, 3, 3)
    assert t > 2
    assert "3/3" in report([better, base])


def test_t_pvalue_matches_t_tables():
    from ritc.tune import t_critical, t_pvalue
    # two-sided 5% critical values from standard t tables (odd and even df)
    for df, tc in [(1, 12.706), (2, 4.303), (7, 2.365), (15, 2.131), (30, 2.042)]:
        assert abs(t_pvalue(tc, df) - 0.05) < 2e-4
        assert abs(t_critical(0.05, df) - tc) < 2e-3
    assert t_pvalue(0.0, 10) == 1.0
    assert t_pvalue(math.inf, 10) == 0.0


def test_report_adjusts_for_number_of_settings():
    from ritc.tune import report
    base = Result({}, {s: 100.0 * s for s in range(1, 9)})
    rows = [Result({"x": k}, {s: 100.0 * s + k + (s % 3) for s in range(1, 9)}) for k in range(1, 6)]
    out = report([base, *rows])
    assert "5 setting(s) compared with the baseline on 8 seeds" in out
    # Bonferroni for 5 tests at df=7 needs |t| ~= 3.50 vs 2.36 for a single test
    assert "3.50" in out and "2.36" in out


def test_sim_passive_fills_are_common_random_numbers():
    """An extra resting order elsewhere must not change another order's fill luck."""
    from ritc.sim.server import Market

    def fills(extra: bool) -> list[int]:
        m = Market("equity", 300, 1, seed=3)
        m.status = "ACTIVE"
        out = []
        for _ in range(40):
            for o in list(m.orders):
                m.cancel(o)
            s = m.secs["SMMR"]
            m.submit("SMMR", "LIMIT", 5000, "BUY", s.bids[0][0])
            if extra:
                m.submit("ATMN", "LIMIT", 5000, "SELL", m.secs["ATMN"].asks[0][0])
            before = s.position
            m.advance()
            out.append(s.position - before)
        return out

    assert fills(False) == fills(True)


def test_wins_ignores_ties():
    rs = [Result({}, {1: 10.0, 2: 5.0}), Result({"a": 2}, {1: 10.0, 2: 9.0})]
    assert wins(rs) == {0: 0, 1: 1}


def test_tail_metrics():
    r = Result({}, {s: float(v) for s, v in enumerate([-40, -10, 5, 20, 30, 50, 60, 80], 1)})
    assert r.cvar == -25.0           # worst 2 of 8
    assert r.negatives == 2


def _queue_market(queue: bool):
    from ritc.sim.server import Market
    m = Market("equity", 300, 1, seed=3, queue=queue)
    m.status = "ACTIVE"
    s = m.secs["SMMR"]
    s.bids, s.asks = [[24.97, 3000], [24.96, 2000]], [[25.03, 3000], [25.04, 2000]]
    m._flow = lambda ticker, action: 2000 if ticker == "SMMR" and action == "BUY" else 0
    return m, s


def test_queue_joins_behind_displayed_shares():
    m, s = _queue_market(True)
    m.submit("SMMR", "LIMIT", 1000, "BUY", 24.97)        # 3,000 shown ahead of us
    m._fill_resting()
    assert s.position == 0                                # flow ate 2,000 of the queue
    m._fill_resting()
    assert s.position == 1000                             # last 1,000 ahead, then us


def test_queue_improving_the_touch_is_first():
    m, s = _queue_market(True)
    m.submit("SMMR", "LIMIT", 1000, "BUY", 24.98)        # inside the spread: nobody ahead
    m._fill_resting()
    assert s.position == 1000


def test_queue_off_is_the_old_model():
    m, s = _queue_market(False)
    m.submit("SMMR", "LIMIT", 1000, "BUY", 24.97)
    m._fill_resting()
    assert s.position == 1000


def test_queue_decay_and_vanished_level():
    m, s = _queue_market(True)
    m.submit("SMMR", "LIMIT", 1000, "BUY", 24.97)
    oid = next(iter(m.orders))
    m._decay_queues()
    assert m.ahead[oid] == 3000 * 0.8                     # QUEUE_CANCEL of the queue leaves
    s.bids = [[24.95, 3000]]                              # the level is gone: nobody left in front
    m._decay_queues()
    assert m.ahead[oid] == 0.0


def test_in_process_transport_answers_like_http():
    from ritc.core.client import RITClient
    from ritc.sim.server import InProcessAdapter, serve
    from ritc.tune import _free_port

    port = _free_port()
    srv, m = serve("equity", port, speed=0, seed=5, block=False)
    try:
        url = f"http://127.0.0.1:{port}/v1"
        http, local = RITClient(url, max_retries=1), RITClient(url, max_retries=1)
        local.adapter = InProcessAdapter(m)
        for c in (http, local):
            assert c.case()["status"] == "ACTIVE"
        assert http.securities() == local.securities() and http.book("SPNG") == local.book("SPNG")
        local.limit_order("SPNG", "BUY", 100, 1.00)              # rests far below the market
        assert [o["price"] for o in http.orders()] == [1.0]      # both see one market
    finally:
        srv.shutdown()
