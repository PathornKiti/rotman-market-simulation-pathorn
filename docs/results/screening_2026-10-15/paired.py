"""
Paired evaluation of the practice profiles on EVERY real journal (any profile the heat ran with): replay each heat under
A_default and under each profile, find the fixed tenders whose answer differs, and value them on the recorded mid path
(path_cf, minus the 0.054 overstatement). A flip counts only when the replay of the heat's OWN config gives the live answer.
H (sprint threshold) changes no answer, so for it: the sprints H starts that A would not, and how far the mid kept running
against the block afterwards (+ = the sprint was right to cross).

    PYTHONPATH=src .venv/bin/python docs/results/screening_2026-10-15/paired.py
"""
import glob
import json
import os
import re
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "docs", "results"))
sys.path.insert(0, os.path.join(ROOT, "docs", "results", "evening_2026-10-14"))
sys.path.insert(0, os.path.join(ROOT, "src"))

OVER = 0.054
FILES = sorted(glob.glob(os.path.expanduser("~/Downloads/log_transfer/*.jsonl"))) + \
    sorted(glob.glob(os.path.join(ROOT, "logs", "data", "*.jsonl")))
PROFILES = {"B": {"strategy.drift_horizon_ticks": 20}, "C": {"strategy.liquidity_model": "flow"},
            "D": {"strategy.risk_aversion": 0.15}, "G": {"strategy.liquidity_model": "flow_guarded"},
            "S": {"strategy.drift_horizon_ticks": 20, "strategy.drift_zero_start": True,
                  "strategy.liquidity_model": "flow_guarded"},
            "H": {"strategy.trend_cross_cost": 0.03}}
OWN = {"A_default": "A", "B_drift_cap20": "B", "C_flow": "C", "D_risk015": "D", "H_sprint003": "H", "S_staging": "S"}
TEN = re.compile(r"t\s*(\d+) TENDER (\d+) (BUY|SELL) (\w+) x[\d.]+ @ (\S+) -> (ACCEPT|decline)")
SPR = re.compile(r"t\s*(\d+) SPRINT (\w+): next .*? against our ([+-]\d+) block")


def run(f):
    from journal_replay import replay
    from path_cf import cf
    rows = [json.loads(line) for line in open(f)]
    if not any(r["k"] == "market" for r in rows):
        return None
    start = next(r for r in rows if r["k"] == "start")
    own = OWN.get(((start.get("cfg") or {}).get("strategy") or {}).get("profile"), "A")
    ticks = {r["tick"]: r for r in rows if r["k"] == "tick"}
    seen = {r["tid"]: r for r in rows if r["k"] == "tender_seen"}
    live = {r["tid"]: r["accept"] for r in rows if r["k"] == "tender_decision"}
    out = {}
    for name, ov in [("A", {})] + list(PROFILES.items()):
        lines = replay(f, dict(ov))
        out[name] = ({int(m.group(2)): m.group(6) == "ACCEPT" for m in map(TEN.match, lines) if m},
                     {(int(m.group(1)), m.group(2), int(m.group(3))) for m in map(SPR.match, lines) if m})
    res = {"heat": os.path.basename(f)[10:25], "own": own, "nlv": next((r["nlv"] for r in rows if r["k"] == "summary"), 0)}

    def mid(t, tk):
        r = ticks.get(t)
        return (r["bid"][tk] + r["ask"][tk]) / 2 if r and r["bid"][tk] and r["ask"][tk] else None

    for name in "BCDGS":
        d, flips = 0.0, []
        for tid, s in seen.items():
            if not s["fixed"] or s["price"] is None or tid not in out["A"][0] or tid not in out[name][0]:
                continue
            a, x = out["A"][0][tid], out[name][0][tid]
            if a == x:
                continue
            ok = tid in live and out[own][0].get(tid) == live[tid]
            v = (cf(ticks, s, 1000)[1] / s["qty"] + cf(ticks, s, 1500)[1] / s["qty"]) / 2 - OVER
            usd = v * s["qty"] * (1 if x else -1)
            flips.append((tid, s["ticker"], "ACCEPT" if x else "decline", round(v, 3), round(usd), ok))
            if ok:
                d += usd
        res[name] = (d, flips)
    extra = []
    a_starts = {(t, tk) for t, tk, _ in out["A"][1]}
    for t, tk, held in out["H"][1]:
        if any(abs(t - ta) <= 2 and tk == tka for ta, tka in a_starts):
            continue
        m0 = mid(t, tk)
        sg = 1 if held > 0 else -1                 # long block: a falling mid is "against"
        cont = [(-sg * (mid(t + k, tk) - m0)) if m0 and mid(t + k, tk) else None for k in (5, 10, 20)]
        extra.append((t, tk, held, cont))
    res["H"] = extra
    return res


if __name__ == "__main__":
    with ProcessPoolExecutor(8) as ex:
        R = [r for r in ex.map(run, FILES) if r]
    print(f"{len(R)} real heats with recorded markets\n")
    for name, lab in (("B", "drift cap 20"), ("C", "flow"), ("D", "risk 0.15"), ("G", "flow_guarded"), ("S", "staging B+Z+G")):
        ds = [r[name][0] for r in R]
        n_ok = sum(1 for r in R for fl in r[name][1] if fl[5])
        n_all = sum(len(r[name][1]) for r in R)
        sd = st.stdev(ds)
        print(f"{name} {lab:14} flips counted {n_ok} of {n_all} | mean {st.mean(ds):+,.0f}/heat  t {st.mean(ds) / (sd / len(ds) ** 0.5) if sd else 0:+.2f}"
              f"  better {sum(d > 0 for d in ds)} worse {sum(d < 0 for d in ds)}  worst {min(ds):+,.0f}  best {max(ds):+,.0f}")
        for r in R:
            for tid, tk, to, v, usd, ok in r[name][1]:
                print(f"     {r['heat']} ({r['own']}) #{tid:<4} {tk} -> {to:7} path {v:+.3f}/sh  {usd:+8,.0f}  {'counted' if ok else 'replay != live'}")
    ex_ = [e for r in R for e in r["H"]]
    print(f"\nH extra sprints (H starts, A does not): {len(ex_)}")
    for i, k in enumerate((5, 10, 20)):
        xs = [e[3][i] for e in ex_ if e[3][i] is not None]
        if len(xs) > 2:
            print(f"   mid kept running against the block +{k:>2} ticks: mean {st.mean(xs):+.3f}  t {st.mean(xs) / (st.stdev(xs) / len(xs) ** 0.5):+.2f}  n {len(xs)}")
    print("\nNLV by the profile each heat RAN (unpaired, different tenders and paths: noisy)")
    for p in "ABCDH":
        xs = [r["nlv"] for r in R if r["own"] == p and r["heat"].startswith("20261015")]
        if xs:
            print(f"   {p}: n {len(xs)}  mean {st.mean(xs):+,.0f}  min {min(xs):+,.0f}  max {max(xs):+,.0f}")
