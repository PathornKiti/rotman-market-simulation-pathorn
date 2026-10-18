"""After a fixed tender, the price often drops AGAINST the holder. How often does it come back?
For each fixed tender (holder side: long after a BUY tender, short after a SELL tender), the worst adverse move of the
mid within the first 50 ticks (the "drop"), then from that low: does the mid get back to (a) the mid at the tender tick,
(b) the tender price (break-even for a holder, before fees), before the bell? How long does it take?"""
import glob, os, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B

crowded = set(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "crowded_heats.txt")).read().split())
rows = []
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    mid, last, ten, nlv, prof, acc = B.load(f)
    if mid["CRZY"][419] is None:
        continue
    name = os.path.basename(f)[10:25]
    for d in ten:
        t, i = d["ticker"], d["tick"]
        sd = 1 if d["action"] == "BUY" else -1
        m0, px = mid[t][i], d["price"]
        if m0 is None or i > 360:
            continue
        w = [sd * (mid[t][k] - m0) for k in range(i, min(419, i + 50) + 1)]      # + = holder's way
        low = min(w)
        kl = i + w.index(low)
        after = [sd * (mid[t][k] - m0) for k in range(kl, 420)]
        back_mid = next((k for k, x in enumerate(after) if x >= 0), None)
        be = sd * (px - m0)                                                    # tender price vs mid0 (holder side)
        back_px = next((k for k, x in enumerate(after) if x >= be), None)
        end = sd * (last[t][419] - m0)
        rows.append(dict(drop=-low, back_mid=back_mid, back_px=back_px, end=end, ticks_left=419 - kl,
                         crowded=name in crowded, taken=d["tid"] in acc))

def show(lab, rs):
    if not rs:
        return
    n = len(rs)
    bm = [r["back_mid"] for r in rs if r["back_mid"] is not None]
    bp = [r["back_px"] for r in rs if r["back_px"] is not None]
    print(f"  {lab:34} n {n:4} | back to tender-time mid {len(bm)/n:4.0%} (median {st.median(bm) if bm else float('nan'):5.0f} ticks after the low)"
          f" | back to tender price {len(bp)/n:4.0%} | at the bell vs mid0 {st.mean(r['end'] for r in rs):+.3f}  (ended above mid0 {sum(r['end']>=0 for r in rs)/n:.0%})")

print("Drop = worst move against the holder within 50 ticks of the tender. 'Back' = mid returns there before the bell.")
for lo, hi in ((0.0, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.50), (0.50, 9)):
    show(f"drop {lo:.2f}-{hi:.2f}", [r for r in rows if lo <= r["drop"] < hi])
print()
for lab, sel in (("drop >= 0.20, all", lambda r: r["drop"] >= 0.2),
                 ("drop >= 0.20, quiet heats", lambda r: r["drop"] >= 0.2 and not r["crowded"]),
                 ("drop >= 0.20, crowded heats", lambda r: r["drop"] >= 0.2 and r["crowded"]),
                 ("drop >= 0.20, tenders the bot took", lambda r: r["drop"] >= 0.2 and r["taken"]),
                 ("drop >= 0.20, low with >= 150 ticks left", lambda r: r["drop"] >= 0.2 and r["ticks_left"] >= 150),
                 ("drop >= 0.20, low with < 100 ticks left", lambda r: r["drop"] >= 0.2 and r["ticks_left"] < 100)):
    show(lab, [r for r in rows if sel(r)])

# ---- decision view: the FIRST tick the holder is down X (within 50 ticks of the tender). Holding on from there vs selling
print("\nAt the first tick the price is down X against the holder (no hindsight): what happens if you keep holding?")
for X in (0.10, 0.20, 0.30, 0.50):
    out = []
    for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
        mid, last, ten, nlv, prof, acc = B.load(f)
        if mid["CRZY"][419] is None:
            continue
        name = os.path.basename(f)[10:25]
        for d in ten:
            t, i = d["ticker"], d["tick"]
            sd = 1 if d["action"] == "BUY" else -1
            m0 = mid[t][i]
            if m0 is None or i > 360:
                continue
            k = next((k for k in range(i, min(419, i + 50) + 1) if sd * (mid[t][k] - m0) <= -X), None)
            if k is None:
                continue
            here = sd * (mid[t][k] - m0)
            nxt = lambda h: sd * (mid[t][min(419, k + h)] - m0) - here           # change from here, + = recovered
            back = next((j for j in range(k, 420) if sd * (mid[t][j] - m0) >= 0), None)
            deeper = next((j for j in range(k, 420) if sd * (mid[t][j] - m0) <= here - 0.30), None)
            out.append(dict(c=name in crowded, b=back is not None, first_back=back is not None and (deeper is None or back < deeper),
                            n20=nxt(20), n50=nxt(50), n100=nxt(100), bell=sd * (last[t][419] - m0) - here))
    for lab, rs in (("all", out), ("quiet", [r for r in out if not r["c"]]), ("crowded", [r for r in out if r["c"]])):
        if not rs:
            continue
        n = len(rs)
        print(f"  down {X:.2f} {lab:7} n {n:4}: back to mid0 {sum(r['b'] for r in rs)/n:4.0%}, back BEFORE another -0.30 {sum(r['first_back'] for r in rs)/n:4.0%}"
              f" | holding on from here: +20 {st.mean(r['n20'] for r in rs):+.3f}  +50 {st.mean(r['n50'] for r in rs):+.3f}  +100 {st.mean(r['n100'] for r in rs):+.3f}  bell {st.mean(r['bell'] for r in rs):+.3f} $/sh")
