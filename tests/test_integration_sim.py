"""
End-to-end: each strategy trades a full case against the offline simulator.
Checks the plumbing (API calls, order flow, shutdown), not profitability -
simulator P&L is noise-driven and not a forecast of competition results.
"""

import socket

import pytest

from ritc.cli import build
from ritc.sim.server import serve
from ritc.strategies import REGISTRY


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("case", sorted(REGISTRY))
def test_strategy_runs_full_case(case, monkeypatch):
    port = free_port()
    monkeypatch.setenv("RIT_URL", f"http://127.0.0.1:{port}/v1")
    srv, market = serve(case, port, speed=150, delay=0.2, seed=7, block=False)
    try:
        runner, _ = build(case, None, live=True)
        runner.interval = 0.005
        runner.run()
        assert market.status == "STOPPED"
        assert runner.s.ex.sent > 0, "strategy never traded"
        assert not market.orders, "orders left resting after shutdown"
    finally:
        srv.shutdown()


def test_dry_run_sends_nothing(monkeypatch):
    port = free_port()
    monkeypatch.setenv("RIT_URL", f"http://127.0.0.1:{port}/v1")
    srv, market = serve("equity", port, speed=150, delay=0.2, seed=7, block=False)
    market.state["blocks"] = False           # an assigned block would move the position without any order
    try:
        runner, _ = build("equity", None, live=False)
        runner.s.ex.dry_run = True
        runner.interval = 0.005
        runner.run()
        assert runner.s.ex.sent == 0
        assert all(s.position == 0 for s in market.secs.values()) and market.cash == 0
    finally:
        srv.shutdown()
