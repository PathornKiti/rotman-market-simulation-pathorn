import socket

import pytest

from ritc import advisor
from ritc.advisor import connection_problem, order_warnings, read_only
from ritc.core.client import RITClient, RITError
from ritc.sim.server import serve


def o(action, qty, filled=0, ticker="CROC"):
    return {"ticker": ticker, "action": action, "quantity": qty, "quantity_filled": filled, "price": 20.0}


def test_order_warnings():
    assert order_warnings("CROC", 10_000, [o("SELL", 5_000)], frozen=False) == []
    assert "through zero" in order_warnings("CROC", 10_000, [o("SELL", 8_000), o("SELL", 4_000)], False)[0]
    assert order_warnings("CROC", 10_000, [o("SELL", 15_000, filled=6_000)], False) == []   # only the unfilled part
    assert "adds to the position" in order_warnings("CROC", 10_000, [o("BUY", 1_000)], False)[0]
    assert "speculation" in order_warnings("CROC", 0, [o("SELL", 1_000)], False)[0]
    assert "front-running" in order_warnings("CROC", 10_000, [o("SELL", 1_000)], True)[0]
    assert order_warnings("CROC", 0, [o("SELL", 1_000, ticker="TAME")], False) == []


def test_read_only_refuses_writes():
    c = RITClient(base_url="http://127.0.0.1:9/v1", max_retries=1)
    read_only(c)
    for call in (lambda: c.limit_order("CROC", "BUY", 1, 20.0), lambda: c.accept_tender(1),
                 lambda: c.decline_tender(1), lambda: c.cancel_all()):
        with pytest.raises(RITError, match="read-only"):
            call()


def test_connection_problem_says_what_to_fix():
    nokey = RITClient(base_url="http://127.0.0.1:9/v1", api_key="", auth="", max_retries=1, timeout=0.5)
    assert "no credentials" in connection_problem(nokey)
    down = RITClient(base_url="http://localhost:9/v1", api_key="k", max_retries=1, timeout=0.5)
    assert "no RIT API" in connection_problem(down)
    remote = RITClient(base_url="http://127.0.0.1:9/v1", auth="Basic x", max_retries=1, timeout=0.5)
    assert "VPN" in connection_problem(remote)


def test_suggest_full_case_sends_nothing(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(advisor, "setup_logging", lambda name: str(tmp_path / f"{name}.log"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("RIT_URL", f"http://127.0.0.1:{port}/v1")
    srv, market = serve("liquidity", port, speed=150, delay=0.2, seed=7, block=False)
    try:
        advisor.suggest(interval=0.005, color=False)
    finally:
        srv.shutdown()
    out = capsys.readouterr().out
    assert "TENDERS" in out and ("ACCEPT" in out or "DECLINE" in out)
    assert market.cash == 0 and all(s.position == 0 for s in market.secs.values()) and not market.orders


def test_reload_env_picks_up_a_pasted_key(monkeypatch, tmp_path):
    from ritc.advisor import reload_env
    from ritc.core import config
    env = tmp_path / ".env"
    env.write_text("RIT_API_KEY=\nRIT_PORT=9999\n")
    monkeypatch.setattr(config, "ENV_PATH", env)
    monkeypatch.delenv("RIT_API_KEY", raising=False)
    monkeypatch.delenv("RIT_URL", raising=False)
    monkeypatch.delenv("RIT_AUTH", raising=False)
    monkeypatch.setenv("RIT_PORT", "9998")                  # set in the real environment: must win over .env
    c = RITClient(api_key="", auth="")
    env.write_text("RIT_API_KEY=ABC123\nRIT_PORT=9999\n")   # the key arrives while we wait
    reload_env(c, real={"RIT_PORT"})
    assert c.api_key == "ABC123" and c.base_url.endswith(":9998/v1")


def test_dma_basic_auth_header():
    c = RITClient(base_url="http://127.0.0.1:9/v1", api_key="", auth="Basic abc")
    assert c.session.headers["Authorization"] == "Basic abc" and "X-API-Key" not in c.session.headers


def test_runner_waits_for_the_server_and_ctrl_c_sends_nothing(monkeypatch):
    """No server yet: the bot must wait (not start without its trader id) and Ctrl-C must exit cleanly."""
    from ritc.cli import build
    from ritc.core import bot
    monkeypatch.setenv("RIT_URL", "http://127.0.0.1:9/v1")
    runner, _ = build("liquidity", None, live=True)
    runner.client.max_retries, runner.client.timeout = 1, 0.2
    naps = []

    def nap(s):
        naps.append(s)
        if len(naps) == 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(bot.time, "sleep", nap)
    runner.run()
    assert len(naps) == 3 and runner.loops == 0 and runner.s.ex.sent == 0


def test_order_lines_show_a_trend_hold():
    """rsi_bot in suggest mode: a block the trend hold keeps shows TREND HOLD (keep it, no order), not a stale order."""
    from types import SimpleNamespace as NS
    book = NS(best_bid=25.00, best_ask=25.02, mid=25.01)
    s = NS(tickers=["TAME"], algo=NS(passive_from=None, blocks={}, passive=set()),
           trend_holds={"TAME": {"on": True, "start": 100, "mid0": 25.31}}, cfg={"execution": {"trend_hold_ticks": 10}})
    snap = NS(positions={"TAME": -53_000}, book=lambda t: book, abs_tick=130)
    view = advisor.Advisor.__new__(advisor.Advisor)
    view.s, view.color = s, False
    out = "\n".join(view.order_lines(snap, {}, []))
    assert "TREND HOLD: keep it, no order (held 30 ticks, mid +0.300 our way)" in out and "LIMIT" not in out
