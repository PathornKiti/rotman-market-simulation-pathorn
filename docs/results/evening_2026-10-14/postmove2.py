import json, glob, statistics as st, math
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
K = (1, 3, 5, 10, 15, 20, 30, 45, 60, 90)
grp = {}
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    d = {r['tid']: r for r in rows if r['k'] == 'tender_decision'}
    mid = lambda t, tk: (ticks[t]['bid'][tk] + ticks[t]['ask'][tk]) / 2 if t in ticks and ticks[t]['bid'][tk] else None
    for tid, s in seen.items():
        if tid not in d: continue
        tk = s['ticker']; hold = 1 if s['action'] == 'BUY' else -1
        m0 = mid(s['tick'], tk)
        if m0 is None: continue
        kind = ('fixed ' + ('taken' if d[tid]['accept'] else 'declined')) if s['fixed'] else 'auction (lost)'
        mv = [None if mid(s['tick'] + k, tk) is None else -hold * (mid(s['tick'] + k, tk) - m0) for k in K]
        grp.setdefault(kind, []).append(mv)
print("mid move AGAINST the tender holder (+ = against), $/share, all 3 stocks pooled, 17 evening heats")
print(f"{'':16}{'n':>4} " + " ".join(f"{'+'+str(k):>12}" for k in K))
for kind in ('fixed taken', 'fixed declined', 'auction (lost)'):
    L = grp[kind]; cells = []
    for i in range(len(K)):
        v = [x[i] for x in L if x[i] is not None]
        m, s = st.mean(v), st.stdev(v); cells.append(f"{m:+.3f}({m/(s/math.sqrt(len(v))):+.1f})")
    print(f"{kind:16}{len(L):>4} " + " ".join(f"{c:>12}" for c in cells))
