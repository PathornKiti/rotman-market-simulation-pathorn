"""
Command-line entry point.

    python -m ritc list                         # the five strategies
    python -m ritc doctor                       # check connection, print raw API data
    python -m ritc monitor                      # read-only dashboard (always safe)
    python -m ritc sim <case>                   # offline simulator on :9999
    python -m ritc run <case>                   # DRY RUN (logs decisions, sends nothing)
    python -m ritc run <case> --live            # trade
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


def build(case: str, cfg_path: str | None, live: bool) -> tuple[Runner, dict]:
    cfg = config.load_case_config(case, cfg_path)
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
                    wind_down_ticks=int(run.get("wind_down_ticks", 5)))
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

    r = sub.add_parser("run", help="run a strategy (dry-run unless --live)")
    r.add_argument("case", choices=sorted(REGISTRY))
    r.add_argument("--live", action="store_true", help="send real orders")
    r.add_argument("--config", help="path to a TOML config (default config/<case>.toml)")
    r.add_argument("--once", action="store_true", help="run a single loop then exit")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=cmd_run)

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
