"""Live heats of 10-18 (both upload folders merged into logs/data): per heat NLV, what the touch accepts made, what the
held blocks made, and a speculation check (position crossing zero without a tender on that ticker).
Usage: python docs/results/rsi_live_2026-10-18/live.py [--since 20261018-000000]"""
import glob, json, os, sys, statistics as st
since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "20261018-000000"
rows_out = []
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    heat = os.path.basename(f)[10:25]
    if heat < since: continue
    R = [json.loads(l) for l in open(f)]
    s = next((r for r in R if r["k"] == "summary"), None)
    if not s: continue
    dec = {r["tid"]: r for r in R if r["k"] == "tender_decision"}
    ten = s.get("tenders") or {}
    touch = [t for t, d in dec.items() if d["accept"] and str(d.get("reason", "")).startswith("touch edge")]
    model = [t for t, d in dec.items() if d["accept"] and "TENDER MODEL" in str(d.get("reason", "")).upper()]
    touch_made = sum((ten.get(str(t)) or {}).get("realized") or 0 for t in touch)
    holds = [r for r in R if r["k"] == "trend_hold"]
    held_blocks = {(h["ticker"], h["tick"] - h["ticks"]) for h in holds if h["ticks"] > 0}
    blocks = s.get("blocks") or []
    held_made = sum(b["made"] for b in blocks if any(tk == b["ticker"] and abs(st0 - b["start_tick"]) <= 40 for tk, st0 in held_blocks))
    # speculation: sign flip of a position with no accepted tender on that ticker in between
    acc_ticks = {}
    for t, d in dec.items():
        if d["accept"]:
            tk = (ten.get(str(t)) or {}).get("ticker")
            acc_ticks.setdefault(tk, []).append(d["tick"])
    spec, prev, last_flip = 0, {}, {}
    for r in R:
        if r["k"] != "tick": continue
        for tk, p in r["pos"].items():
            q = prev.get(tk, 0)
            if q and p and (q > 0) != (p > 0):
                if not any(prev_t <= a <= r["tick"] + 1 for a in acc_ticks.get(tk, []) for prev_t in [last_flip.get(tk, -99)]):
                    spec += 1
            if p != q: last_flip[tk] = r["tick"]
            prev[tk] = p
    lt = next((r for r in reversed(R) if r["k"] == "tick"), None)
    end_pos = sum(abs(v) for v in lt["pos"].values()) if lt else -1
    rows_out.append(dict(heat=heat, prof=s.get("profile"), nlv=s["nlv"], crowd=(s.get("crowd") or {}).get("crowded"),
                         taken=sum(1 for t in ten.values() if t.get("kind") == "fixed" and t.get("won")),
                         touch=len(touch), touch_made=touch_made, model=len(model), held=len(held_blocks), held_made=held_made,
                         lose=sum(b["made"] < 0 for b in blocks), worst=min([b["made"] for b in blocks] or [0]), spec=spec, endpos=end_pos))
print(f"{'heat':16}{'profile':13}{'NLV':>9} {'crowd':5} {'taken':>5} {'touch':>5} {'touch$':>8} {'held':>4} {'held$':>9} {'lose':>4} {'worstblk':>9} spec endpos")
for r in rows_out:
    print(f"{r['heat']:16}{r['prof']:13}{r['nlv']:>9,.0f} {'YES' if r['crowd'] else '':5} {r['taken']:>5} {r['touch']:>5} {r['touch_made']:>8,.0f} {r['held']:>4} {r['held_made']:>9,.0f} {r['lose']:>4} {r['worst']:>9,.0f} {r['spec']:>4} {r['endpos']:>6}")
print()
for p in sorted({r["prof"] for r in rows_out}):
    x = [r["nlv"] for r in rows_out if r["prof"] == p]
    print(f"{p:13} n {len(x):2}  mean {st.mean(x):>9,.0f}  median {st.median(x):>9,.0f}  worst {min(x):>9,.0f}  best {max(x):>9,.0f}  >=70k {sum(v >= 70000 for v in x)}/{len(x)}")
