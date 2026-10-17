"""Mid move in the tender holder's favour after each fixed tender (+ = holding would gain), split by whether our bot
took it (our own unwind is then in the path) or declined it (path = rivals only)."""
import glob, math, statistics as st, sys
sys.path.insert(0, "docs/results/rsi_hold_2026-10-17")
from backtest import load

H = (10, 25, 50, 100, 200, "end")
rows = {True: {h: [] for h in H}, False: {h: [] for h in H}}
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    mid, last, tenders, nlv, profile, accepted = load(f)
    if mid["CRZY"][419] is None:
        continue
    for d in tenders:
        t, i = d["ticker"], d["tick"]
        side = 1 if d["action"] == "BUY" else -1
        m0 = mid[t][i]
        if m0 is None:
            continue
        for h in H:
            j = 419 if h == "end" else i + h
            if j > 419:
                continue
            p = last[t][419] if h == "end" else mid[t][j]
            rows[d["tid"] in accepted][h].append(side * (p - m0))
for took, lab in ((True, "taken by our bot"), (False, "declined (no own impact)")):
    print(f"{lab}:")
    for h in H:
        xs = rows[took][h]
        print(f"  +{h!s:>4} ticks  n {len(xs):4}  mean {st.mean(xs):+.3f} $/sh  t {st.mean(xs)/(st.stdev(xs)/math.sqrt(len(xs))):+5.1f}"
              f"  P(our way) {sum(x>0 for x in xs)/len(xs):.0%}  sd {st.stdev(xs):.2f}")
