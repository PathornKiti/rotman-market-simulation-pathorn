"""Run from the repo root: PYTHONPATH=src .venv/bin/python docs/results/exits_2026-10-15/exits.py  (edit R for the resting speed)."""
# Exit rules on the RECORDED price paths + order books (others' orders), every single-tender fixed block.
# steady: rest-like exit of R shares/tick at the mid. stop X: as steady, but once the mid is X against the tender mid,
# cross what is left into the recorded book. front f: cross f of the block at once, the rest steady.
import ast, glob, json, os, statistics as st
from collections import defaultdict
R, FEE = 1250, 0.02
lit = lambda x: x if isinstance(x, (dict, list)) else ast.literal_eval(x)
def walk(side, n):          # $ / share away from the touch-mid for crossing n shares into one side of the book
    need, cost, got = n, 0.0, 0
    for o in side:
        q = min(need, o[1]); cost += q * o[0]; got += q; need -= q
        if need <= 0: break
    return cost / got if got else None, need <= 0
rows_all = []
for f in sorted(glob.glob("logs/data/*.jsonl")):
    rows = [json.loads(l) for l in open(f)]
    s = next((r for r in rows if r["k"] == "summary"), None)
    if not s or not any(r["k"] == "market" for r in rows): continue
    ticks = {r["tick"]: r for r in rows if r["k"] == "tick"}
    seen = {r["tid"]: r for r in rows if r["k"] == "tender_seen"}
    books, me = {}, None
    for r in rows:
        if r["k"] == "market" and "books" in r and "case" in r:
            try:
                t = int(lit(r["case"])["tick"]); me = lit(r["trader"]).get("trader_id", me) if r.get("trader") else me
                if t not in books: books[t] = lit(r["books"])
            except Exception: pass
    def mid(t, tk):
        r = ticks.get(min(t, 419))
        return (r["bid"][tk] + r["ask"][tk]) / 2 if r and r["bid"][tk] and r["ask"][tk] else None
    def cross(t, tk, sg, n):  # cost/share vs mid of crossing n shares at tick t (long block sells into bids)
        b = books.get(t, {}).get(tk); m = mid(t, tk)
        if not b or m is None: return 0.15
        side = b["bids"] if sg > 0 else b["asks"]
        lv = [(abs(o["price"] - m), o["quantity"] - o["quantity_filled"]) for o in side if o.get("trader_id") != me]
        c, ok = walk(sorted(lv), n)
        return c if ok else (c or 0.15) + 0.10      # book too thin: penalise
    for b in s["blocks"]:
        if len(b["tenders"]) != 1: continue
        tid = b["tenders"][0]; t = s["tenders"].get(str(tid)); sn = seen.get(tid)
        if not t or t["kind"] != "fixed" or not sn: continue
        tk, q, t0, m0 = sn["ticker"], int(sn["qty"]), sn["tick"] + 1, sn["mid"]
        sg = 1 if sn["action"] == "BUY" else -1
        edge = t["vs_mid"]
        def steady(start, n, stop=None):
            """$ from exiting n shares from tick `start` at R/tick at the mid; optional stop (X against m0)."""
            pnl, left, k = 0.0, n, start
            while left > 0:
                m = mid(k, tk) or m0
                if stop is not None and sg * (m - m0) <= -stop:
                    c = cross(k, tk, sg, left); return pnl + left * (sg * (m - m0) - c)
                x = min(R, left); pnl += x * sg * (m - m0); left -= x; k += 1
            return pnl
        res = {"heat": os.path.basename(f)[10:25], "made": b["made"], "q": q}
        base = q * (edge - FEE)
        res["steady"] = base + steady(t0, q)
        for X in (0.10, 0.15, 0.20, 0.30):
            res[f"stop {X:.2f}"] = base + steady(t0, q, X)
        for fr in (0.25, 0.5, 1.0):
            n = int(q * fr); c = cross(t0, tk, sg, n); m = mid(t0, tk) or m0
            res[f"front {fr:.0%}"] = base + n * (sg * (m - m0) - c) + steady(t0, q - n)
        rows_all.append(res)
keys = [k for k in rows_all[0] if k not in ("heat", "made", "q")]
loss = [r for r in rows_all if r["made"] < 0]
print(f"{len(rows_all)} blocks, {len({r['heat'] for r in rows_all})} heats (path model, R={R} sh/tick; same model for every rule)\n")
print(f"{'rule':12} {'$/block':>8} {'vs steady $/heat':>16} {'t':>6} {'18+ losing blocks $':>20} {'other blocks $':>15} {'worst block':>12}")
H = defaultdict(lambda: defaultdict(float))
for r in rows_all:
    for k in keys: H[r["heat"]][k] += r[k]
for k in keys:
    d = [H[h][k] - H[h]["steady"] for h in H]; sd = st.stdev(d)
    print(f"{k:12} {st.mean(r[k] for r in rows_all):8,.0f} {st.mean(d):+16,.0f} {st.mean(d)/(sd/len(d)**.5) if sd else 0:+6.2f} "
          f"{sum(r[k] for r in loss):+20,.0f} {sum(r[k] for r in rows_all if r['made'] >= 0)/len(H):+15,.0f}/heat {min(r[k] for r in rows_all):+12,.0f}")
print(f"\nlive (what the bot made): {st.mean(r['made'] for r in rows_all):,.0f}/block; losing blocks total {sum(r['made'] for r in loss):+,.0f}")
tot = {k: [H[h][k] for h in H] for k in keys}
print("\nper HEAT (sum of its blocks):")
for k in ("steady", "front 25%", "front 50%", "stop 0.10"):
    x = tot[k]; print(f"  {k:10} mean {st.mean(x):+8,.0f}  sd {st.stdev(x):7,.0f}  worst heat {min(x):+8,.0f}  heats < $20k: {sum(v < 20000 for v in x)}")
