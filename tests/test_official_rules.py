"""The simulator follows the rules the official RITC case packages state (docs/OFFICIAL_RULES.md)."""

import math

from ritc.pricing.news import parse_vol_news
from ritc.sim.server import Market, delta_penalty


def active(case: str, seed: int = 1) -> Market:
    m = Market(case, 300, 2 if case == "derivatives" else 1, seed=seed)
    m.status = "ACTIVE"
    return m


# ------------------------------------------------------------------ volatility case
def test_delta_penalty_formula():
    # (|delta| - limit) x pct per second over the limit, nothing inside it.
    assert delta_penalty(5500, 5000, 0.005) == 2.5
    assert delta_penalty(-5500, 5000, 0.005) == 2.5
    assert delta_penalty(4999, 5000, 0.005) == 0.0


def test_derivatives_universe_fees_and_limits():
    m = active("derivatives")
    opts = [s for s in m.secs.values() if s.kind == "OPTION"]
    assert len(opts) == 40                                  # 2 expiries x 10 strikes x call/put
    assert {int(m.state[s.ticker][2]) for s in opts} == set(range(45, 55))
    assert all(s.fee == 2.00 for s in opts) and m.secs["RTM"].fee == 0.02
    cash = m.cash
    o = m.submit("RTM1C50", "MARKET", 10, "BUY", None)
    assert math.isclose(cash - m.cash, 10 * 100 * o["vwap"] + 10 * 2.00)    # premium x 100 + $2/contract
    m.submit("RTM", "MARKET", 1000, "BUY", None)
    lim = {row["name"]: row for row in m.limits_view()}
    assert lim["options"]["gross"] == 10 and lim["etf"]["gross"] == 1000   # each group counts only its tickers


def test_derivatives_penalty_accrues_only_over_limit():
    m = active("derivatives")
    m.advance()                                             # tick 1: delta-limit news
    assert m.penalty == 0.0
    m.submit("RTM", "MARKET", 10_000, "BUY", None)          # 10,000 delta vs a 7,000 limit
    m.advance()
    assert math.isclose(m.penalty, 3000 * 0.005, rel_tol=1e-9)
    assert math.isclose(m.score(), m.nlv() - m.penalty)


def test_derivatives_news_is_official_wording_and_forecasts_next_week():
    m = active("derivatives", seed=4)
    seen = []
    for _ in range(300):
        m.advance()
        seen.extend(n["body"] for n in m.news[len(seen):])
    first = parse_vol_news(seen[0])
    assert first.delta_limit == 7000 and math.isclose(first.penalty_pct, 0.005)
    # Every mid-week range must contain the vol that is then announced for the next week.
    weekly = [parse_vol_news(b) for b in seen[1:]]
    for rng, nxt in zip(weekly, weekly[1:]):
        if rng.forecast_lo is not None and nxt.realized is not None:
            assert rng.forecast_lo - 1e-9 <= nxt.realized <= rng.forecast_hi + 1e-9


# ------------------------------------------------------------------ ETF (algo) case
def test_etf_is_usd_quoted_and_nlv_is_cad():
    m = active("etf")
    usd = m.secs["USD"].mid
    nav_usd = (m.secs["BULL"].mid + m.secs["BEAR"].mid) / usd
    assert m.secs["RITC"].ccy == "USD" and abs(m.secs["RITC"].mid - nav_usd) < 0.5
    m.submit("RITC", "MARKET", 1000, "BUY", None)
    paid_usd = -m.secs["USD"].position                      # the ETF is paid for in USD...
    assert paid_usd > 0 and m.cash == 0.0                   # ...not CAD
    lim = m.limits_view()[0]
    assert lim["gross"] == 2000                             # the ETF counts twice


def test_etf_closes_out_at_fair_value():
    m = active("etf")
    m.submit("RITC", "MARKET", 1000, "BUY", None)
    m.secs["RITC"].mid += 1.0                               # a premium the close-out must ignore
    nav_usd = (m.secs["BULL"].mid + m.secs["BEAR"].mid) / m.secs["USD"].mid
    usd_before = m.secs["USD"].position
    m._close_out()
    assert m.secs["RITC"].position == 0
    assert math.isclose(m.secs["USD"].position - usd_before, 1000 * nav_usd)


def test_etf_case_sends_ritc_tenders_settled_in_usd():
    m = active("etf")
    for _ in range(10):
        m.advance()
    assert any(t["ticker"] == "RITC" and t["is_fixed_bid"] for t in m.tenders.values())


def test_etf_tenders_do_not_change_the_price_path():
    a, b = active("etf", seed=7), active("etf", seed=7)
    b.tender_rng.random()                                   # perturb only the tender stream
    for _ in range(60):
        a.advance()
        b.advance()
    assert a.secs["RITC"].mid == b.secs["RITC"].mid and a.secs["BULL"].mid == b.secs["BULL"].mid


# ------------------------------------------------------------------ liability auctions
def test_competitive_tender_fills_only_past_the_reserve():
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from ritc.sim.server import make_handler

    m = active("liability", 3)
    def auction(v: dict) -> bool:                 # competitive, not winner-take-all
        return not v["is_fixed_bid"] and not v["caption"].startswith("Winner")
    while not any(auction(t) for t in m.tenders.values()):
        m.advance()
    tid, t = next((k, v) for k, v in m.tenders.items() if auction(v))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(m))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def bid(price: float) -> bool:
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/v1/tenders/{tid}?price={price}",
                                         method="POST")
            return json.loads(urllib.request.urlopen(req).read())["success"]
        buy = t["action"] == "BUY"
        stingy = t["_reserve"] - 0.01 if buy else t["_reserve"] + 0.01
        m.tenders[tid] = dict(t)
        assert not bid(stingy)                    # short of the client's reserve: rejected
        m.tenders[tid] = dict(t)
        assert bid(t["_reserve"])                 # at (or past) it: filled at our price
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ equity block transfers
def test_equity_assigns_unannounced_blocks_that_drift_against_the_holder():
    m = active("equity", 2)
    for _ in range(300):
        m.advance()
        moved = [s for s in m.secs.values() if s.position]
        if moved:
            break
    s = moved[0]
    assert abs(s.position) in (5_000, 10_000, 15_000)
    assert [n["headline"] for n in m.news] == ["Position limit"]        # nothing announces the block
    d = m.state["block_drift"][0]
    assert d[1] == s.ticker and (d[2] < 0) == (s.position > 0)        # the drift runs against the block


# ------------------------------------------------------------------ RITC 2026 liquidity risk case
def test_liability_trades_the_sub_heats_tickers_not_the_configs(monkeypatch):
    # RITC 2026: each sub-heat has DIFFERENT stocks (RITC/COMP, TRNT/MTRL, BLU/RED/GRN, ...). The config
    # names CRZY/TAME; a bot that only unwinds configured tickers is left holding accepted tenders
    # ($10/share uncovered fine). Rename the simulator's stocks and check every tender is unwound.
    from ritc.cli import build
    from ritc.sim.server import InProcessAdapter, serve
    from ritc.tune import _free_port

    port = _free_port()
    monkeypatch.setenv("RIT_URL", f"http://127.0.0.1:{port}/v1")
    srv, m = serve("liability", port, speed=0, seed=3, block=False)
    try:
        m.secs = {new: m.secs[old] for old, new in (("CRZY", "BLU"), ("TAME", "RED"))}
        for t, s in m.secs.items():
            s.ticker = t
            s.fee = 0.04 if t == "BLU" else 0.03
        # 2027 brief: holding to the bell is free (close-out at the last price), so the bot may end with a
        # position on purpose; switch the hold off to check that every tender on these tickers IS unwound.
        runner, _ = build("liability", None, live=True, overrides={"execution.close_hold_ticks": 0})
        runner.client.adapter = InProcessAdapter(m)
        runner.interval = 0.0
        held = []

        def tick():
            if runner.loops % 4 == 0:
                if m.tick == m.tpp - 1:
                    held.append({t: s.position for t, s in m.secs.items()})
                m.advance()

        runner.on_loop = tick
        runner.run()
        assert {o["ticker"] for o in m.done_orders} >= {"BLU", "RED"}     # tenders taken AND unwound
        assert held and not any(held[-1].values())                           # flat before the close-out
        assert runner.s.fees == {"CRZY": 0.02, "TAME": 0.02, "CROC": 0.02, "BLU": 0.04, "RED": 0.03}   # server's fees
        assert runner.s.risk.room("BLU", "BUY", {"BLU": 249_000}) < 1_000           # counted in the limit
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ RITC 2026 algorithmic market making case
def test_equity_is_the_2026_universe_with_its_rebates():
    m = active("equity")
    assert sorted(m.secs) == ["ATMN", "SMMR", "SPNG", "WNTR"]
    assert all(s.mid == 25.0 and s.fee == 0.02 for s in m.secs.values())
    assert {t: s.rebate for t, s in m.secs.items()} == {"SPNG": 0.01, "SMMR": 0.02, "ATMN": 0.015, "WNTR": 0.025}


def test_equity_aggregate_limit_is_announced_and_fined_at_each_close():
    from ritc.pricing.news import parse_position_limit

    m = active("equity", seed=4)
    m.state["blocks"] = False
    m.advance()
    assert parse_position_limit(m.news[0]["body"]) == m.state["agg_limit"]
    m.secs["SPNG"].position = m.state["agg_limit"] + 700
    m.secs["WNTR"].position = -300                         # |short| counts too
    while m.tick < 59:
        m.advance()
    assert m.penalty == 0                                   # only assessed at a close
    m.advance()                                             # tick 60: the close
    assert m.penalty == 10 * 1000


def test_position_limit_news_parser():
    from ritc.pricing.news import parse_position_limit

    assert parse_position_limit("The aggregate position limit for this week is 15,000 shares") == 15000
    assert parse_position_limit("Your CRO set an aggregate position limit of 20000 shares") == 20000
    assert parse_position_limit("The delta limit for this sub-heat is 10,000") is None
    assert parse_position_limit("Stocks rallied 1,200 points") is None


def test_close_cuts_largest_positions_first():
    from ritc.strategies.equity import close_cuts, ticks_to_close

    assert close_cuts({"A": 9000, "B": -6000, "C": 1000}, 13500) == [("A", "SELL", 2500)]
    assert close_cuts({"A": 4000, "B": -6000}, 1000) == [("B", "BUY", 6000), ("A", "SELL", 3000)]
    assert close_cuts({"A": 4000}, 15000) == []
    assert [ticks_to_close(t, 60) for t in (1, 55, 59, 60, 61)] == [59, 5, 1, 0, 59]
