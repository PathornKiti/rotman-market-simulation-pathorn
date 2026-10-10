"""
Liquidity Risk Case stress test: the bot across every adverse scenario we can name.

    python -m ritc stress                        # all scenarios, seeds 1-16
    python -m ritc stress --seeds 32 --first-seed 301 --only gap
    python -m ritc stress --set execution.hold_risk_budget=20000 --template

Each scenario is the brief's market with one (or several) of its unknowns pushed the wrong way
(`RITC_STRESS` knobs in sim/server.py, docs/GAP_ANALYSIS.md). Every scenario runs the SAME seeds,
so rows are comparable and a config change can be judged scenario by scenario. Per scenario:
mean, worst seed, CVaR25 (mean of the worst quarter), losing seeds, fines, peak |position|, max
intraday drawdown, tenders taken / seen, shares still held at the bell. `--template` adds the
official "Tender trade template" from the case package as a benchmark.
"""

from __future__ import annotations

import json
import logging
import os
import statistics as st
from concurrent.futures import ProcessPoolExecutor

# name: (RITC_STRESS, hostile, queue mode, bot loops per tick, group)
SCENARIOS: dict[str, tuple[str, float, bool, int, str]] = {
    "base (brief)":              ("", 0, False, 4, "core"),
    "volatility x2":             ("vol=2", 0, False, 4, "core"),
    "thin books x0.5":           ("depth=0.5", 0, False, 4, "core"),
    "worse tenders -10c":        ("edge=-0.10", 0, False, 4, "core"),
    "bigger tenders x2":         ("size=2", 0, False, 4, "core"),
    "tenders twice as often":    ("gap=6", 0, False, 4, "core"),
    "booking delay 3s":          ("book=3", 0, False, 4, "core"),
    "strict front-running":      ("strict_fr=1", 0, False, 4, "core"),
    "hostile market":            ("", 1, False, 4, "hostile"),
    "hostile anchored":          ("anchor=1", 1, False, 4, "hostile"),
    "everything at once":        ("vol=1.5,depth=0.6,edge=-0.05,size=1.5,book=2,strict_fr=1", 1, False, 4, "hostile"),
    "everything anchored":       ("vol=1.5,depth=0.6,edge=-0.05,size=1.5,book=2,strict_fr=1,anchor=1", 1, False, 4,
                                  "hostile"),
    # ---- gaps between the simulator and the real server (docs/GAP_ANALYSIS.md)
    "resting fills pay $0.02":   ("maker_fee=1", 0, False, 4, "gap"),
    "spread 2c + queue":         ("spread=0.02", 0, True, 4, "gap"),
    "spread 1c + queue":         ("spread=0.01", 0, True, 4, "gap"),
    "scarce resting fills":      ("flow=0.3", 0, False, 4, "gap"),
    "close 5c against holder":   ("close_slip=0.05", 0, False, 4, "gap"),
    "desks dump at the bell":    ("bell=0.20", 0, False, 4, "gap"),
    "very slow bot (1 loop/s)":  ("", 0, False, 1, "gap"),
    "hostile crowd at expiry":   ("anchor=1,crowd_at_expiry=1", 1, False, 4, "gap"),
    "vol regimes (hidden)":      ("vol_regime=2.5", 0, False, 4, "gap"),
    "real-world combined":       ("maker_fee=1,spread=0.02,close_slip=0.03,bell=0.10,flow=0.6", 0, True, 2, "gap"),
    "real-world + hostile":      ("maker_fee=1,spread=0.02,close_slip=0.03,bell=0.10,flow=0.6,anchor=1", 1, True, 2,
                                  "gap"),
}


def _track(m, peak: dict) -> None:
    g = sum(abs(s.position) for s in m.secs.values())
    peak["gross"] = max(peak["gross"], g)
    # Inventory-risk metrics (Cartea & Jaimungal-style fine tuning): average shares held and the $ 1-sd risk of
    # holding them to the bell, sum |q| x sigma x sqrt(ticks left), averaged over the heat and at its peak.
    left = max(m.tpp - m.tick, 0) ** 0.5
    risk = sum(abs(s.position) * s.sigma * left for s in m.secs.values())
    peak["n"] = peak.get("n", 0) + 1
    peak["g_sum"] = peak.get("g_sum", 0) + g
    peak["r_sum"] = peak.get("r_sum", 0.0) + risk
    peak["r_pk"] = max(peak.get("r_pk", 0.0), risk)
    v = m.nlv() - m.penalty
    peak["hi"] = max(peak["hi"], v)
    peak["dd"] = max(peak["dd"], peak["hi"] - v)


def run_bot(seed: int, name: str, overrides: dict, config_path: str | None = None) -> dict:
    from .cli import build
    from .sim.server import InProcessAdapter, serve
    from .tune import _free_port

    stress, hostile, queue, loops, _ = SCENARIOS[name]
    logging.disable(logging.CRITICAL)
    os.environ["RITC_STRESS"] = stress
    port = _free_port()
    os.environ["RIT_URL"] = f"http://127.0.0.1:{port}/v1"
    srv, m = serve("liability", port, speed=0, delay=0.2, seed=seed, block=False, hostile=hostile, queue=queue)
    peak, held = {"gross": 0, "hi": 0.0, "dd": 0.0}, {}
    try:
        r, _ = build("liability", config_path, live=True, overrides=overrides)
        r.client.adapter = InProcessAdapter(m)
        r.interval = 0.0
        if os.environ.get("RITC_ORACLE_VOL"):
            # Value-of-information test: the bot gets the TRUE current $ vol per tick instead of its GARCH
            # estimate. No vol model can beat this, so oracle - GARCH bounds what a better one could add.
            k = float(os.environ["RITC_ORACLE_VOL"])            # 1 = exact; 0.5 / 2 = a biased estimate
            r.s.price_vol = lambda t, snap: k * m.secs[t].sigma if t in m.secs else 0.0

        def tick():
            if r.loops % loops == 0:
                if m.tick == m.tpp:
                    held.update({t: s.position for t, s in m.secs.items() if s.position})
                m.advance()
                _track(m, peak)
        r.on_loop = tick
        r.run()
        return {"score": m.score(), "fine": m.penalty, "seen": len(r.s.seen), "taken": len(r.s.taken),
                "held": sum(abs(v) for v in held.values()), **peak}
    finally:
        srv.shutdown()


def run_template(seed: int, name: str) -> dict:
    """Official 'Tender trade template': decline before half time, accept fixed tenders after; sell 3,000/s."""
    from .sim.server import Market

    stress, hostile, queue, _, _ = SCENARIOS[name]
    os.environ["RITC_STRESS"] = stress
    m = Market("liability", 420, 1, seed, hostile, queue)
    m.status = "ACTIVE"
    peak, taken, seen = {"gross": 0, "hi": 0.0, "dd": 0.0}, 0, 0
    while m.status == "ACTIVE":
        m.advance()
        if m.status != "ACTIVE":
            break
        if 10 < m.tick < 410:
            for tid, t in sorted(m.tenders.items(), key=lambda kv: kv[1]["expires"])[:1]:
                seen += 1
                m.tenders.pop(tid)
                sg = 1 if t["action"] == "BUY" else -1
                if m.tick >= 205 and t["is_fixed_bid"] and not m._over_limit(t["ticker"], sg * t["quantity"]):
                    taken += 1
                    m.bookings.append((m.abs_tick + int(m.stress["book"]), t, t["price"]))
            for tk, s in m.secs.items():
                if s.position:
                    m.submit(tk, "MARKET", min(abs(s.position), 3000), "SELL" if s.position > 0 else "BUY", None)
        _track(m, peak)
    return {"score": m.score(), "fine": m.penalty, "seen": seen, "taken": taken, "held": 0, **peak}


def _job(args: tuple) -> tuple[str, str, int, dict]:
    who, name, seed, overrides, cfg = args
    try:
        res = run_bot(seed, name, overrides, cfg) if who == "bot" else run_template(seed, name)
    except Exception as exc:                  # noqa: BLE001 - one crashed run must not kill the table
        res = {"error": repr(exc)}
    return who, name, seed, res


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    sc = sorted(r["score"] for r in ok)
    if not sc:
        return {"n": 0, "errors": len(rows)}
    q = max(1, len(sc) // 4)
    return {"n": len(sc), "errors": len(rows) - len(ok), "mean": st.fmean(sc), "sd": st.pstdev(sc),
            "worst": sc[0], "cvar25": st.fmean(sc[:q]), "losing": sum(x < 0 for x in sc),
            "fine": st.fmean(r["fine"] for r in ok), "fine_max": max(r["fine"] for r in ok),
            "gross": int(max(r["gross"] for r in ok)), "dd": st.fmean(r["dd"] for r in ok),
            "taken": st.fmean(r["taken"] for r in ok), "seen": st.fmean(r["seen"] for r in ok),
            "held": int(max(r["held"] for r in ok)),
            "avg_gross": st.fmean(r.get("g_sum", 0) / max(1, r.get("n", 1)) for r in ok),
            "avg_risk": st.fmean(r.get("r_sum", 0.0) / max(1, r.get("n", 1)) for r in ok),
            "peak_risk": st.fmean(r.get("r_pk", 0.0) for r in ok)}


def stress(seeds: range, only: str | None = None, overrides: dict | None = None, template: bool = False,
           config_path: str | None = None, workers: int | None = None, out: str | None = None) -> dict:
    wanted = [w.strip() for w in only.split(",")] if only else []
    names = [n for n, v in SCENARIOS.items() if not wanted or v[4] in wanted or n in wanted]
    whos = ["bot"] + (["template"] if template else [])
    jobs = [(w, n, s, overrides or {}, config_path) for n in names for w in whos for s in seeds]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(_job, jobs))
    table: dict = {}
    for who in whos:
        for n in names:
            table[f"{who}|{n}"] = summarise([r for w, nn, s, r in res if w == who and nn == n])
    if out:
        with open(out, "w") as f:
            json.dump({"table": table, "runs": res}, f)
    return table


def sweep(seeds: range, key: str, values: list, only: str | None = None, overrides: dict | None = None,
          config_path: str | None = None, workers: int | None = None) -> dict:
    """Fine tuning: the same scenarios and seeds for each value of one setting -> {value: table}."""
    return {v: stress(seeds, only, {**(overrides or {}), key: v}, False, config_path, workers) for v in values}


def print_frontier(key: str, tables: dict) -> None:
    print(f"\nFRONTIER over {key}: expected P&L vs risk (each row: same seeds). avg/peak $risk = 1-sd $ risk of the "
          f"inventory held to the bell; sd = heat-to-heat P&L spread")
    print(f"{'scenario':<26}{key.split('.')[-1]:>14}{'mean':>9}{'sd':>8}{'worst':>9}{'CVaR25':>9}{'lose':>6}"
          f"{'avg sh':>9}{'avg$risk':>10}{'peak$risk':>10}{'held@end':>9}")
    names = list(next(iter(tables.values())))
    for key_name in names:
        for v, table in tables.items():
            x = table[key_name]
            if not x.get("n"):
                continue
            print(f"{key_name.split('|', 1)[1]:<26}{str(v):>14}{x['mean']:>9,.0f}{x['sd']:>8,.0f}{x['worst']:>9,.0f}"
                  f"{x['cvar25']:>9,.0f}{x['losing']:>3}/{x['n']:<2}{x['avg_gross']:>9,.0f}{x['avg_risk']:>10,.0f}"
                  f"{x['peak_risk']:>10,.0f}{x['held']:>9,}")
        print()


def print_table(table: dict) -> None:
    print(f"{'scenario':<26}{'who':<9}{'mean':>9}{'worst':>9}{'CVaR25':>9}{'lose':>6}{'fine':>7}"
          f"{'peak|pos|':>10}{'maxDD':>8}{'taken/seen':>12}{'held@end':>9}")
    for key, v in table.items():
        who, name = key.split("|", 1)
        if not v.get("n"):
            print(f"{name:<26}{who:<9} all runs failed")
            continue
        print(f"{name:<26}{who:<9}{v['mean']:>9,.0f}{v['worst']:>9,.0f}{v['cvar25']:>9,.0f}"
              f"{v['losing']:>3}/{v['n']:<2}{v['fine']:>7,.0f}{v['gross']:>10,}{v['dd']:>8,.0f}"
              f"{v['taken']:>6.1f}/{v['seen']:<5.1f}{v['held']:>9,}"
              + (f"  ({v['errors']} errors)" if v["errors"] else ""))
