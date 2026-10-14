"""What, AT DECISION TIME, predicts the move against the holder over the next 20 ticks? (fixed tenders, 17 evening heats)"""
import json, glob, statistics as st, math
import numpy as np
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
X, Y, meta = [], [], []
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    d = {r['tid']: r for r in rows if r['k'] == 'tender_decision'}
    mid = lambda t, tk: (ticks[t]['bid'][tk] + ticks[t]['ask'][tk]) / 2 if t in ticks and ticks[t]['bid'][tk] else None
    for tid, s in seen.items():
        if tid not in d or not s['fixed']: continue
        tk = s['ticker']; hold = 1 if s['action'] == 'BUY' else -1; t0 = s['tick']
        m0, mp, m20 = mid(t0, tk), mid(t0 - 10, tk), mid(t0 + 20, tk)
        if None in (m0, mp, m20) or not s.get('bid_depth10'): continue
        exit_d = s['bid_depth10'] if hold > 0 else s['ask_depth10']      # we sell into bids after a BUY tender
        other = s['ask_depth10'] if hold > 0 else s['bid_depth10']
        asym = math.log((other + 1000) / (exit_d + 1000))                # + = our exit side is the thin side
        edge = hold * (m0 - s['price'])
        X.append([1.0, -hold * (m0 - mp), asym, edge, 1.0 if d[tid]['accept'] else 0.0]); Y.append(-hold * (m20 - m0))
        meta.append((f[-11:-6], tid, tk, d[tid]['accept']))
X, Y = np.array(X), np.array(Y)
names = ['intercept', 'pre-10 trend against', 'exit side thin (log depth ratio)', 'tender edge', 'we took it']
beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
res = Y - X @ beta; s2 = res @ res / (len(Y) - X.shape[1]); se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
print(f"next-20-tick move against the holder, n={len(Y)} fixed tenders, R2 {1 - res@res/((Y-Y.mean())@(Y-Y.mean())):.2f}")
for n, b, e in zip(names, beta, se): print(f"  {n:34} {b:+.3f}  (t {b/e:+.1f})")
print("exit side thin (asym>0.5):", round(float(Y[X[:,2] > 0.5].mean()), 3), "n", int((X[:,2] > 0.5).sum()), "| balanced/thick:", round(float(Y[X[:,2] <= 0.5].mean()), 3), "n", int((X[:,2] <= 0.5).sum()))
