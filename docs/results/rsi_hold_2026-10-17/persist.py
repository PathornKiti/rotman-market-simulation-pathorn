"""Does a trend keep going? Past move over L ticks vs the next H ticks, all recorded heats, all 3 stocks,
non-overlapping windows. Reported: corr, and the mean next move in the trend's direction when |past| is large."""
import glob, math, statistics as st, sys
sys.path.insert(0, "docs/results/rsi_hold_2026-10-17")
from backtest import load, rsi_series

paths = []
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    mid, last, ten, nlv, prof, acc = load(f)
    if mid["CRZY"][419] is None:
        continue
    paths.append((f.split("liability-")[1][:8], mid))

def t_(xs):
    return st.mean(xs) / (st.stdev(xs) / math.sqrt(len(xs)))

print("past L -> next H: corr | mean next move in trend direction when |past| >= thr ($/sh), n, t, P(continues)")
for L in (10, 20, 50, 100):
    for H in (10, 20, 50, 100):
        xs, ys = [], []
        for day, mid in paths:
            for t in mid:
                p = mid[t]
                for i in range(max(L, 20), 420 - H, H):
                    xs.append(p[i] - p[i - L]); ys.append(p[i + H] - p[i])
        c = st.correlation(xs, ys)
        thr = sorted(abs(x) for x in xs)[int(0.8 * len(xs))]  # top 20% strongest trends
        cont = [math.copysign(1, x) * y for x, y in zip(xs, ys) if abs(x) >= thr]
        print(f"  L {L:3} -> H {H:3}: corr {c:+.2f} | top-20% |past| >= {thr:.2f}: next {st.mean(cont):+.3f}  n {len(cont):4}  "
              f"t {t_(cont):+5.1f}  P {sum(y > 0 for y in cont)/len(cont):.0%}")

print("\nRSI(10) on 10-tick candles, next H ticks (+ = continues the RSI side, - = reverts):")
for H in (20, 50, 100):
    for lo, hi in ((30, 70), (20, 80)):
        cont = []
        for day, mid in paths:
            for t in mid:
                r = rsi_series(mid[t])
                for i in range(109, 420 - H, H):
                    if i in r and (r[i] >= hi or r[i] <= lo):
                        cont.append((1 if r[i] >= hi else -1) * (mid[t][i + H] - mid[t][i]))
        print(f"  RSI >= {hi} / <= {lo}, next {H:3}: {st.mean(cont):+.3f} $/sh in the RSI direction, n {len(cont)}, t {t_(cont):+.1f}")
