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
    while not any(not t["is_fixed_bid"] for t in m.tenders.values()):
        m.advance()
    tid, t = next((k, v) for k, v in m.tenders.items() if not v["is_fixed_bid"])
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
    assert abs(s.position) in (5_000, 10_000, 15_000) and not m.news
    d = m.state["block_drift"][0]
    assert d[1] == s.ticker and (d[2] < 0) == (s.position > 0)        # the drift runs against the block
