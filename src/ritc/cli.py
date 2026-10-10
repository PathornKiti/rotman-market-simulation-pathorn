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
    python -m ritc stress [--only gap]          # liability bot across every adverse scenario
    python -m ritc record                       # READ-ONLY: record a practice heat to logs/record-*.jsonl
    python -m ritc calibrate logs/record-*.jsonl  # measured vol / depth / tenders / booking delay
    python -m ritc report [logs/liability-*.log]  # liability: gap report per run -> reports/*_local|_live.html/.xlsx
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

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
    from .tune import parse_value
    sets = {k.strip(): parse_value(v.strip()) for k, _, v in (x.partition("=") for x in a.set or [])}
    runner, _ = build(a.case, a.config, a.live, overrides=sets)
    from .core.journal import Journal
    runner.journal = runner.s.ex.journal = Journal(str(path).rsplit(".", 1)[0] + ".jsonl")
    print(f"log file: {path}" + (f"  overrides: {sets}" if sets else ""))
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
    results = tune(a.case, parse_grid(a.grid or []), seeds, a.speed, a.workers, a.config, hostile=a.hostile,
                   queue=a.queue)
    print("\n" + report(results))
    return 0


def cmd_sim(a: argparse.Namespace) -> int:
    from .sim.server import serve
    serve(a.case, a.port, a.speed, a.delay, a.seed, hostile=a.hostile, queue=a.queue)
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


def cmd_stress(a: argparse.Namespace) -> int:
    from .stress import print_table, stress
    from .tune import parse_value
    sets = {k.strip(): parse_value(v.strip()) for k, _, v in (x.partition("=") for x in a.set or [])}
    seeds = range(a.first_seed, a.first_seed + a.seeds)
    print(f"stress: seeds {seeds.start}-{seeds.stop - 1}" + (f", overrides {sets}" if sets else "")
          + (f", only {a.only}" if a.only else ""), flush=True)
    if a.sweep:
        from .stress import print_frontier, sweep
        key, _, vals = a.sweep.partition("=")
        values = [parse_value(v.strip()) for v in vals.split(",") if v.strip()]
        print_frontier(key.strip(), sweep(seeds, key.strip(), values, a.only, sets, a.config, a.workers))
        return 0
    print_table(stress(seeds, a.only, sets, a.template, a.config, a.workers, a.out))
    return 0


def cmd_record(a: argparse.Namespace) -> int:
    from .recorder import record
    print(f"saved {record(a.out, a.interval)}")
    return 0


def cmd_calibrate(a: argparse.Namespace) -> int:
    import glob

    from .recorder import calibrate, print_report
    paths = [p for f in a.files for p in (sorted(glob.glob(f)) or [f])]     # Windows cmd doesn't expand *
    reps = []
    for path in paths:
        rep = calibrate(path)
        print_report(rep)
        reps.append(rep)
        if a.json:
            Path(a.json).write_text(json.dumps(rep, indent=2))
    if len(reps) > 1:
        from .recorder import print_crowd, summarise_crowd
        pooled: dict[str, list[float]] = {}
        for rep in reps:
            for key, y in rep.get("crowd_raw", {}).items():
                pooled.setdefault(key, []).extend(y)
        print(f"\nPOOLED crowd event study over {len(reps)} recordings (decide on these, not on one heat):")
        print_crowd(summarise_crowd(pooled))
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    from .postmortem import main as report_main
    args = list(a.logs) + ["--out", a.out, "--mode", a.mode]
    if a.no_counterfactuals:
        args.append("--no-counterfactuals")
    if a.workers:
        args += ["--workers", str(a.workers)]
    if a.stress:
        args += ["--stress", "--seeds", str(a.seeds), "--first-seed", str(a.first_seed)] + (["--only", a.only]
                                                                                           if a.only else [])
    return report_main(args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ritc", description="RITC trading bots")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list strategies").set_defaults(fn=cmd_list)
    sub.add_parser("doctor", help="check the API connection").set_defaults(fn=cmd_doctor)
    st_ = sub.add_parser("stress", help="liability: the bot across every adverse scenario (docs/POSTMORTEM.md)")
    st_.add_argument("--seeds", type=int, default=16)
    st_.add_argument("--first-seed", type=int, default=1)
    st_.add_argument("--only", help="comma-separated groups (core / hostile / gap) and/or scenario names")
    st_.add_argument("--sweep", metavar="SECTION.KEY=V1,V2,...",
                     help="fine tuning: run the scenarios once per value and print P&L vs inventory risk")
    st_.add_argument("--set", action="append", metavar="SECTION.KEY=VALUE", help="config override for the bot")
    st_.add_argument("--template", action="store_true", help="also run the official tender template")
    st_.add_argument("--config")
    st_.add_argument("--workers", type=int)
    st_.add_argument("--out", help="write the table and every run to this JSON file")
    st_.set_defaults(fn=cmd_stress)
    rc = sub.add_parser("record", help="READ-ONLY: record a practice heat (books, tenders, positions)")
    rc.add_argument("--out", help="output .jsonl (default logs/record-<time>.jsonl)")
    rc.add_argument("--interval", type=float, default=0.5,
                    help="seconds between snapshots (raise to 1 if the bot log shows `rate limited`)")
    rc.set_defaults(fn=cmd_record)
    ca = sub.add_parser("calibrate", help="measure vol / depth / tenders / booking delay from a recording")
    ca.add_argument("files", nargs="+")
    ca.add_argument("--json", help="also write the full report (incl. every tender) to this file")
    ca.set_defaults(fn=cmd_calibrate)

    rp = sub.add_parser("report", help="liability: post-trade gap report per bot log (.html + .xlsx in reports/)")
    rp.add_argument("logs", nargs="*", help="bot logs (default: logs/liability-*.log)")
    rp.add_argument("--out", default="reports")
    rp.add_argument("--mode", choices=["auto", "local", "live"], default="auto",
                    help="local = the offline simulator, live = the RIT server (auto: detected from the log)")
    rp.add_argument("--no-counterfactuals", action="store_true", help="skip the per-tender what-if replays (faster)")
    rp.add_argument("--workers", type=int)
    rp.add_argument("--stress", action="store_true",
                    help="every `ritc stress` scenario, all seeds aggregated per scenario")
    rp.add_argument("--seeds", type=int, default=16)
    rp.add_argument("--first-seed", type=int, default=1)
    rp.add_argument("--only", help="--stress: groups (core / hostile / gap) and/or scenario names")
    rp.set_defaults(fn=cmd_report)

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
    r.add_argument("--set", action="append", metavar="SECTION.KEY=VALUE",
                   help="override one config value for this run, e.g. --set strategy.min_profit_per_share=0.05")
    r.add_argument("--once", action="store_true", help="run a single loop then exit")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=cmd_run)

    tu = sub.add_parser("tune", help="grid-search / A-B test parameters on the simulator")
    tu.add_argument("case", choices=sorted(REGISTRY))
    tu.add_argument("--grid", action="append", help="section.key=v1,v2 (repeatable)")
    tu.add_argument("--seeds", type=int, default=16)
    tu.add_argument("--first-seed", type=int, default=1)
    tu.add_argument("--speed", type=float, default=0.0,
                    help="0 = lock-step, reproducible (default); N = real-time at N ticks/second")
    tu.add_argument("--workers", type=int)
    tu.add_argument("--hostile", type=float, default=0.0,
                    help="simulate manipulative competitors: spoofing, pump-and-dump, liquidity vacuums, "
                         "penny-jumping, crowded tenders (0 = off, 1 = full)")
    tu.add_argument("--queue", action="store_true",
                    help="price-time priority: resting orders queue behind the displayed book")
    tu.add_argument("--config")
    tu.set_defaults(fn=cmd_tune)

    s = sub.add_parser("sim", help="offline RIT simulator")
    s.add_argument("case", choices=sorted(REGISTRY))
    s.add_argument("--port", type=int, default=9999)
    s.add_argument("--speed", type=float, default=4.0, help="ticks per second")
    s.add_argument("--delay", type=float, default=3.0)
    s.add_argument("--seed", type=int)
    s.add_argument("--hostile", type=float, default=0.0, help="manipulative competitors (0 = off, 1 = full)")
    s.add_argument("--queue", action="store_true", help="price-time priority behind the displayed book")
    s.set_defaults(fn=cmd_sim)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
