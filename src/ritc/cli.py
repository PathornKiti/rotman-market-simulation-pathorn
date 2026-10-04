"""
Command-line entry point.

    python -m ritc list                         # the five strategies
    python -m ritc doctor                       # check connection, print raw API data
    python -m ritc monitor                      # read-only dashboard (always safe)
    python -m ritc analyze [TICKER ...]         # GARCH / mean-reversion diagnostics from price history
    python -m ritc sim <case>                   # offline simulator on :9999
    python -m ritc run <case>                   # DRY RUN (logs decisions, sends nothing)
    python -m ritc run <case> --live            # trade
    python -m ritc tune <case> --grid section.key=a,b --seeds 8   # A/B test on the simulator
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .core import config
from .core.bot import Context, Runner
from .core.client import RITClient, RITError
from .core.execution import Executor
from .core.logging_setup import setup as setup_logging
from .core.risk import RiskManager
from .strategies import REGISTRY


def cmd_list(_: argparse.Namespace) -> int:
    for name, cls in REGISTRY.items():
        doc = (sys.modules[cls.__module__].__doc__ or "").strip().splitlines()[0]
        print(f"  {name:<12} {doc}")
    return 0


def set_path(cfg: dict, dotted: str, value) -> None:
    """set_path(cfg, "strategy.min_profit_per_share", 0.05)"""
    *parents, leaf = dotted.split(".")
    node = cfg
    for k in parents:
        node = node.setdefault(k, {})
    node[leaf] = value


def build(case: str, cfg_path: str | None, live: bool, overrides: dict | None = None) -> tuple[Runner, dict]:
    cfg = config.load_case_config(case, cfg_path)
    for k, v in (overrides or {}).items():
        set_path(cfg, k, v)
    client = RITClient()
    dry = not live and config.env_bool("RIT_DRY_RUN", True)
    if live:
        dry = False
    case_cfg = cfg.get("case", {})
    ex = Executor(client, dry_run=dry, max_order_size={k: int(v) for k, v in case_cfg.get("max_order", {}).items()},
                  default_max_order=int(case_cfg.get("default_max_order", 5000)),
                  decimals=int(case_cfg.get("decimals", 2)))
    risk = RiskManager.from_config(cfg)
    strat = REGISTRY[case](Context(client, ex, risk, cfg))
    run = cfg.get("run", {})
    runner = Runner(strat, interval=float(run.get("interval", config.env_float("RIT_INTERVAL", 0.25))),
                    wind_down_ticks=int(run.get("wind_down_ticks", 5)),
                    max_drawdown=float(run.get("max_drawdown", 0.0)),
                    drawdown_soft_start=float(run.get("drawdown_soft_start", 0.5)),
                    drawdown_floor=float(run.get("drawdown_floor", 0.25)),
                    feed_poll=float(run.get("feed_poll", 0.1)))
    return runner, cfg


def cmd_run(a: argparse.Namespace) -> int:
    path = setup_logging(a.case, a.verbose)
    runner, _ = build(a.case, a.config, a.live)
    print(f"log file: {path}")
    if not runner.s.ex.dry_run:
        print(">>> LIVE TRADING - Ctrl-C cancels all orders and stops <<<")
    try:
        lim = runner.client.limits()
        runner.s.risk.update_from_api(lim)
    except RITError:
        pass
    runner.run(once=a.once)
    return 0


def cmd_tune(a: argparse.Namespace) -> int:
    from .tune import parse_grid, report, tune
    seeds = list(range(a.first_seed, a.first_seed + a.seeds))
    results = tune(a.case, parse_grid(a.grid or []), seeds, a.speed, a.workers, a.config)
    print("\n" + report(results))
    return 0


def cmd_sim(a: argparse.Namespace) -> int:
    from .sim.server import serve
    serve(a.case, a.port, a.speed, a.delay, a.seed)
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    print(config.describe())
    c = RITClient(max_retries=1)
    ok = True
    for name, fn in [("case", c.case), ("trader", c.trader), ("limits", c.limits),
                     ("securities", c.securities), ("news", lambda: c.news(limit=5)),
                     ("tenders", c.tenders), ("open orders", c.orders)]:
        try:
            data = fn()
            print(f"\n--- {name} ---\n{json.dumps(data, indent=2)[:1500]}")
        except RITError as exc:
            ok = False
            print(f"\n--- {name} --- FAILED: {exc}")
    if not ok:
        print("\nSome calls failed. Is the RIT client running and logged in? Is RIT_API_KEY right?"
              "\nOffline? Start the simulator:  python -m ritc sim equity")
    return 0 if ok else 1


def cmd_analyze(a: argparse.Namespace) -> int:
    """Time-series diagnostics per ticker: should we use GARCH? Is it mean-reverting?"""
    from .pricing.timeseries import Garch11, fit_ou, log_returns, volatility_clusters

    c = RITClient(max_retries=1)
    tickers = a.tickers or [s["ticker"] for s in c.securities()]
    print(f"{'ticker':<10}{'n':>5}{'vol/tick':>10}{'LB Q(r^2)':>11}{'clusters':>10}"
          f"{'alpha':>8}{'beta':>8}{'OU t':>8}{'half-life':>11}{'mean-rev':>10}")
    for t in tickers:
        closes = [float(h["close"]) for h in reversed(c.history(t, limit=a.limit)) if h.get("close")]
        r = log_returns(closes)
        if len(r) < 30:
            print(f"{t:<10}{len(r):>5}  not enough history yet")
            continue
        clusters, q = volatility_clusters(r)
        vol = (sum(x * x for x in r) / len(r)) ** 0.5
        try:
            g = Garch11.fit(r)
            ab = f"{g.alpha:>8.3f}{g.beta:>8.3f}"
        except ValueError:
            ab = f"{'-':>8}{'-':>8}"
        ou = fit_ou(closes)
        hl = f"{ou.half_life:>11.1f}" if ou and ou.half_life < 1e6 else f"{'inf':>11}"
        print(f"{t:<10}{len(r):>5}{vol:>10.5f}{q:>11.1f}{('YES' if clusters else 'no'):>10}{ab}"
              f"{(ou.t_stat if ou else 0):>8.2f}{hl}{('YES' if ou and ou.significant() else 'no'):>10}")
    print("\nclusters=YES -> try vol_model = \"garch\" (equity) / garch_weight > 0 (derivatives, no-news periods)"
          "\nmean-rev=YES -> ou_weight leans equity quotes toward the OU forecast automatically")
    return 0


def cmd_monitor(a: argparse.Namespace) -> int:
    c = RITClient(max_retries=1)
    try:
        while True:
            case, tr, secs = c.case(), c.trader(), c.securities()
            lines = [f"{case.get('name')}  period {case.get('period')} tick {case.get('tick')}/"
                     f"{case.get('ticks_per_period')}  {case.get('status')}   NLV {tr.get('nlv', 0):,.2f}",
                     f"{'ticker':<10}{'bid':>10}{'ask':>10}{'spread':>8}{'pos':>10}{'unrl':>12}"]
            for s in secs:
                b, k = s.get("bid") or 0, s.get("ask") or 0
                lines.append(f"{s['ticker']:<10}{b:>10.2f}{k:>10.2f}{(k - b):>8.2f}"
                             f"{s.get('position', 0):>10}{s.get('unrealized', 0) or 0:>12,.0f}")
            for lim in c.limits():
                lines.append(f"limit {lim.get('name')}: gross {lim.get('gross')}/{lim.get('gross_limit')} "
                             f"net {lim.get('net')}/{lim.get('net_limit')}")
            print("\033[2J\033[H" + "\n".join(lines), flush=True)
            if a.once:
                break
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass
    except RITError as exc:
        print(f"API error: {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ritc", description="RITC trading bots")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list strategies").set_defaults(fn=cmd_list)
    sub.add_parser("doctor", help="check the API connection").set_defaults(fn=cmd_doctor)

    m = sub.add_parser("monitor", help="read-only dashboard")
    m.add_argument("--interval", type=float, default=1.0)
    m.add_argument("--once", action="store_true")
    m.set_defaults(fn=cmd_monitor)

    an = sub.add_parser("analyze", help="time-series diagnostics: GARCH / mean reversion per ticker")
    an.add_argument("tickers", nargs="*")
    an.add_argument("--limit", type=int, default=1000, help="max history points per ticker")
    an.set_defaults(fn=cmd_analyze)

    r = sub.add_parser("run", help="run a strategy (dry-run unless --live)")
    r.add_argument("case", choices=sorted(REGISTRY))
    r.add_argument("--live", action="store_true", help="send real orders")
    r.add_argument("--config", help="path to a TOML config (default config/<case>.toml)")
    r.add_argument("--once", action="store_true", help="run a single loop then exit")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=cmd_run)

    tu = sub.add_parser("tune", help="grid-search / A-B test parameters on the simulator")
    tu.add_argument("case", choices=sorted(REGISTRY))
    tu.add_argument("--grid", action="append", help="section.key=v1,v2 (repeatable)")
    tu.add_argument("--seeds", type=int, default=5)
    tu.add_argument("--first-seed", type=int, default=1)
    tu.add_argument("--speed", type=float, default=0.0,
                    help="0 = lock-step, reproducible (default); N = real-time at N ticks/second")
    tu.add_argument("--workers", type=int)
    tu.add_argument("--config")
    tu.set_defaults(fn=cmd_tune)

    s = sub.add_parser("sim", help="offline RIT simulator")
    s.add_argument("case", choices=sorted(REGISTRY))
    s.add_argument("--port", type=int, default=9999)
    s.add_argument("--speed", type=float, default=4.0, help="ticks per second")
    s.add_argument("--delay", type=float, default=3.0)
    s.add_argument("--seed", type=int)
    s.set_defaults(fn=cmd_sim)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
