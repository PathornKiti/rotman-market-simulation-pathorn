"""The simulator follows the rules the official RITC case packages state (docs/OFFICIAL_RULES.md)."""


from ritc.sim.server import Market


def active(case: str, seed: int = 1) -> Market:
    m = Market(case, 300, 1, seed=seed)
    m.status = "ACTIVE"
    return m




# ------------------------------------------------------------------ ETF (algo) case


# ------------------------------------------------------------------ liquidity auctions
def test_competitive_tender_fills_only_past_the_reserve():
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from ritc.sim.server import make_handler

    m = active("liquidity", 3)
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


# ------------------------------------------------------------------ RITC 2026 liquidity risk case
def test_liquidity_trades_the_sub_heats_tickers_not_the_configs(monkeypatch):
    # RITC 2026: each sub-heat has DIFFERENT stocks (RITC/COMP, TRNT/MTRL, BLU/RED/GRN, ...). The config
    # names CRZY/TAME; a bot that only unwinds configured tickers is left holding accepted tenders
    # ($10/share uncovered fine). Rename the simulator's stocks and check every tender is unwound.
    from ritc.cli import build
    from ritc.sim.server import InProcessAdapter, serve
    from ritc.tune import _free_port

    port = _free_port()
    monkeypatch.setenv("RIT_URL", f"http://127.0.0.1:{port}/v1")
    srv, m = serve("liquidity", port, speed=0, seed=3, block=False)
    try:
        m.secs = {new: m.secs[old] for old, new in (("CRZY", "BLU"), ("TAME", "RED"))}
        for t, s in m.secs.items():
            s.ticker = t
            s.fee = 0.04 if t == "BLU" else 0.03
        # 2027 brief: holding to the bell is free (close-out at the last price), so the bot may end with a
        # position on purpose; switch the hold off to check that every tender on these tickers IS unwound.
        runner, _ = build("liquidity", None, live=True, overrides={"execution.close_hold_ticks": 0})
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


