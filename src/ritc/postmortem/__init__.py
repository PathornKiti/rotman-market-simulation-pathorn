"""
Post-trade gap reports for the Liquidity Risk Case bot.

    python -m ritc report                                  # every logs/liability-*.log
    python -m ritc report logs/liability-20261010-105313.log --mode live
    python -m ritc report --no-counterfactuals             # faster (no per-tender what-ifs)
    python -m ritc report --stress                         # every stress scenario, every seed (stress_report.py)

Writes, per run, `reports/liability-<date>-<time>_<local|live>.html` and `.xlsx`, plus `reports/index.html`.

`local` = the run traded the offline simulator (its tenders match a simulator seed, or it ran faster than RIT's
1 tick per second, or on a port other than RIT's 9999); `live` = the RIT server. For local runs the report
rebuilds the simulated market from the seed (true mid, hidden auction reserve, winner-take-all rival,
windows) and replays the CURRENT bot on it, so it can show what each decision was worth and which gaps the
current version has already closed. Live runs get the log-only checks.
"""

from __future__ import annotations

import time
from pathlib import Path

from .findings import IMPROVEMENTS, VERSIONS, run_checks
from .logparse import Run, parse
from .replay import (
    PARTS,
    counterfactuals,
    decompose,
    execution,
    fines,
    flip_category,
    inventory,
    replay,
    tender_table,
    window_fills,
)
from .simmatch import align, cover_variants, find_seeds, stream

__all__ = ["build_model", "classify", "main", "parse", "write_reports"]


def classify(run: Run, seeds: list[dict]) -> tuple[str, str]:
    """('local' | 'live', why)."""
    if seeds:
        return "local", "tender stream matches simulator seed " + ", ".join(str(s["seed"]) for s in seeds)
    c = run.clock()
    if c and c[1] > 1.5:
        return "local", f"ran at {c[1]:.1f} ticks/s (RIT runs 1 tick/s)"
    if run.ports and run.ports != {9999}:
        return "local", f"API on port {', '.join(map(str, sorted(run.ports)))} (RIT uses 9999)"
    if not run.tenders:
        return "local", "no tenders: nothing to compare (default local)"
    return "live", "no simulator seed explains the tenders and the clock runs at ~1 tick/s"


def _scenarios() -> dict:
    from ..stress import SCENARIOS
    return {k: (v[0], v[1]) for k, v in SCENARIOS.items()}


def build_model(path: str | Path, mode: str = "auto", with_counterfactuals: bool = True,
                workers: int | None = None, progress=print) -> dict:
    run = parse(path)
    seeds = [] if mode == "live" else find_seeds(run, workers=workers)
    auto_mode, why = classify(run, seeds)
    if mode != "auto" and mode != auto_mode:
        why = f"set with --mode {mode} (auto-detect said {auto_mode}: {why})"
    mode = auto_mode if mode == "auto" else mode
    if mode == "live":
        seeds = []
    progress(f"  {run.path.name}: {mode} ({why})")
    scen = _scenarios()
    positional = bool(run.dry_run) and not run.orders
    streams, gt, variants, gt_index, vstreams = [], {}, {}, {}, {}
    for s in seeds:
        st_ = stream(s["seed"], books=True)
        al = align(s["keys"], st_["tenders"], positional)
        streams.append((s["seed"], st_["tenders"], al))
        for k, i in al.items():
            gt.setdefault(k, st_["tenders"][i])
            gt_index.setdefault(k, (s["seed"], i))
        variants[s["seed"]] = cover_variants(run, s["seed"], s["keys"], scen)
        for v in variants[s["seed"]]:
            vstreams[(s["seed"], v["scenario"])] = stream(s["seed"], v["stress"], v["hostile"])["tenders"]
    if run.version == "v1-instant":                    # v1 answers on sight: decision time ~ arrival tick
        for t in run.tenders.values():
            k = (t.tid, t.action, t.ticker, t.qty, t.fixed)
            if k in gt and t.decisions:
                run.anchors.append((min(d.wall for d in t.decisions), gt[k]["tick"]))
    checks = run_checks(run, streams, gt, mode == "local")

    # Current bot, live, on the same simulated market(s): at most four replays per log.
    replays = []
    for s in seeds:
        for v in variants[s["seed"]]:
            if len(replays) >= 4:
                break
            t0 = time.time()
            r = replay(s["seed"], v["stress"], v["hostile"])
            rows = tender_table(r)
            rep = {"seed": s["seed"], "scenario": v["scenario"], "stress": v["stress"], "hostile": v["hostile"],
                   "score": r["score"], "decomposition": decompose(r), "execution": execution(r),
                   "inventory": inventory(r), "fines": fines(r), "tenders": rows, "log": r["log"],
                   "limit_rejects": r["limit_rejects"], "window_fills": window_fills(r), "cf": None}
            if with_counterfactuals and not replays:
                cf = counterfactuals(r, workers)
                by_tid = {row["tid"]: row for row in rows}
                for c in cf:
                    c["category"] = flip_category(by_tid[c["tid"]])
                rep["cf"] = cf
            replays.append(rep)
            progress(f"    replay seed {s['seed']} [{v['scenario']}]: ${r['score']:,.0f}"
                     f"{' + counterfactuals' if rep['cf'] is not None else ''} ({time.time() - t0:.0f}s)")

    # One ledger row per logged tender, with the simulator's truth where known.
    ledger = []
    for t in run.tender_list():
        k = (t.tid, t.action, t.ticker, t.qty, t.fixed)
        g = gt.get(k)
        sign = 1 if t.action == "BUY" else -1
        g0 = g or {}
        row = {"tid": t.tid, "action": t.action, "ticker": t.ticker, "qty": t.qty, "fixed": t.fixed,
               "seen_tick": t.seen_tick, "expires": t.expires if t.expires is not None else g0.get("expires"),
               "arrival_tick": g0.get("tick"), "kind": g0.get("kind", "private" if t.fixed else "auction"),
               "mid_arrival": (g or {}).get("mid"), "reserve": (g or {}).get("reserve"),
               "rival": (g or {}).get("rival"), "stream": gt_index.get(k), "book": (g or {}).get("book"),
               "price": t.prices[0] if t.prices else None, "decisions": []}
        for d in t.decisions:
            px = d.price
            edge = sign * (g["mid"] - px) if (g and px is not None) else None
            dec = {"tick": run.tick_at(d.wall), "accept": d.accept, "pps": d.pps, "bid": d.bid, "price": px,
                   "edge": edge, "reason": d.reason,
                   "rejected": any(abs(b - (d.bid or -1)) < 1e-9 for _, b in t.rejected)}
            if g and d.bid is not None:
                dec["bid_vs_reserve"] = sign * (d.bid - g["reserve"])
                dec["bid_vs_rival"] = sign * (d.bid - g["rival"]) if g["rival"] is not None else None
            if px is not None and k in gt_index:
                st_seed, idx = gt_index[k]
                vs = variants.get(st_seed, [])
                matches = [v["scenario"] for v in vs
                           if idx < len(vstreams[(st_seed, v["scenario"])])
                           and vstreams[(st_seed, v["scenario"])][idx]["price"] is not None
                           and abs(vstreams[(st_seed, v["scenario"])][idx]["price"] - px) < 1e-6]
                dec["variant"] = " / ".join(matches) if matches else None
                vt = vstreams.get((st_seed, matches[0])) if matches else None
                if vt is not None:                       # truth of the variant this bot traded
                    dec["edge"] = sign * (vt[idx]["mid"] - px)
            row["decisions"].append(dec)
        ledger.append(row)
    if gt:
        from .findings import valuation_from_ledger
        checks = [valuation_from_ledger(ledger) if c.id == "valuation" else c for c in checks]
    from .findings import closeout_check, execution_check, limits_check, windows_check
    primary = replays[0] if replays else None
    checks += [execution_check(run, primary), windows_check(primary), closeout_check(primary),
               limits_check(run, primary)]

    from .live import analyse
    journal = analyse(Path(path).with_suffix(".jsonl"))      # written by `ritc run` next to the log
    if journal:
        progress(f"    journal: {len(journal['series'])} ticks, {len(journal['tenders'])} tenders, "
                 f"{len(journal['struggles'])} struggle types")
    c = run.clock()
    first_tick = run.tick_at(run.t0) if c else None
    last_tick = run.tick_at(run.t1) if c else None
    return {
        "file": str(path), "name": run.path.name, "stamp": run.stamp, "mode": mode, "mode_why": why,
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "run": {"bots": run.bots, "dry_run": run.dry_run, "version": run.version,
                "version_label": VERSIONS.get(run.version, run.version), "lines": run.lines,
                "duration": run.duration, "first_tick": first_tick, "last_tick": last_tick,
                "ticks_per_s": c[1] if c else None, "orders": len(run.orders),
                "order_shares": sum(o[4] for o in run.orders), "blocks": len(run.blocks),
                "api_errors": len(run.api_errors), "slow_loops": len(run.slow_loops), "crashed": run.crashed,
                "shutdown": run.shutdown, "loop_stats": run.loop_stats[-1] if run.loop_stats else None,
                "tca": run.tca, "crowd_lines": len(run.crowd), "vol": run.vol[-1][1] if run.vol else None,
                "tenders": len(run.tenders), "decisions": sum(len(t.decisions) for t in run.tenders.values()),
                "accepts": sum(1 for t in run.tenders.values() for d in t.decisions if d.accept),
                "rejected_bids": sum(len(t.rejected) for t in run.tenders.values())},
        "market": {"seeds": [s["seed"] for s in seeds],
                   "variants": {str(k): v for k, v in variants.items()},
                   "streams": [{"seed": sd, "tenders": len(tl), "matched": len(al)} for sd, tl, al in streams]},
        "checks": [c.__dict__ for c in checks],
        "journal": journal,
        "ledger": ledger,
        "replays": replays,
        "improvements": IMPROVEMENTS,
        "parts": PARTS,
        "orders": [{"tick": run.tick_at(o[0]), "side": o[2], "ticker": o[3], "qty": o[4], "px": o[5]}
                   for o in run.orders],
        "blocks": [{"tick": run.tick_at(b[0]), "kind": b[1], "ticker": b[2], "start": b[3], "target": b[4],
                    "deadline": b[5]} for b in run.blocks],
        "crowd": [{"tick": run.tick_at(x[0]), "ticker": x[1], "move": x[2], "k": x[3], "mean": x[4], "sd": x[5],
                   "n": x[6]} for x in run.crowd],
    }


def write_reports(paths: list[str], out_dir: str = "reports", mode: str = "auto", with_counterfactuals: bool = True,
                  workers: int | None = None, progress=print, stress: dict | None = None) -> list[Path]:
    """One report (.html, .xlsx, .json) for every run in `paths` (+ the stress test, if given)."""
    from .bundle import write_bundle
    models = [build_model(p, mode, with_counterfactuals, workers, progress) for p in paths]
    return write_bundle(models, stress, out_dir, progress)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import glob
    ap = argparse.ArgumentParser(prog="ritc report", description="post-trade gap reports for the liability bot")
    ap.add_argument("logs", nargs="*", help="bot logs (default: logs/liability-*.log)")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--mode", choices=["auto", "local", "live"], default="auto")
    ap.add_argument("--no-counterfactuals", action="store_true")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--stress", action="store_true",
                    help="report the current bot through every `ritc stress` scenario (all seeds per scenario)")
    ap.add_argument("--seeds", type=int, default=16)
    ap.add_argument("--first-seed", type=int, default=1)
    ap.add_argument("--only", help="stress: comma-separated groups (core / hostile / gap) and/or scenario names")
    a = ap.parse_args(argv)
    stress = None
    if a.stress:
        from .stress_report import run_stress
        stress = run_stress(list(range(a.first_seed, a.first_seed + a.seeds)), a.only, a.workers)
    paths = [p for f in (a.logs or ["logs/liability-*.log"]) for p in (sorted(glob.glob(f)) or [f])]
    print(f"report: {len(paths)} log(s) -> {a.out}/")
    for f in write_reports(paths, a.out, a.mode, not a.no_counterfactuals, a.workers, stress=stress):
        print(f"  wrote {f}")
    return 0
