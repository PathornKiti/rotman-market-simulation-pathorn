"""Is the post-tender move a CROWD (other teams unwinding the same tender) or just the trend the bot saw and declined?
Split by the 10-tick trend BEFORE the tender (signed: + = already running against the holder)."""
import json, glob, statistics as st, math
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
rows_out = []
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    d = {r['tid']: r for r in rows if r['k'] == 'tender_decision'}
    mid = lambda t, tk: (ticks[t]['bid'][tk] + ticks[t]['ask'][tk]) / 2 if t in ticks and ticks[t]['bid'][tk] else None
    for tid, s in seen.items():
        if tid not in d: continue
        tk = s['ticker']; hold = 1 if s['action'] == 'BUY' else -1; t0 = s['tick']
        m0, mp = mid(t0, tk), mid(t0 - 10, tk)
        m20 = mid(t0 + 20, tk)
        if None in (m0, mp, m20): continue
        kind = ('taken' if d[tid]['accept'] else 'declined') if s['fixed'] else 'auction'
        rows_out.append(dict(kind=kind, pre=-hold * (m0 - mp), post=-hold * (m20 - m0), tk=tk, edge=(hold * (m0 - s['price']) if s['price'] else None)))
def show(lab, v):
    if len(v) < 3: print(f"{lab:44} n={len(v)}"); return
    m, s = st.mean(v), st.stdev(v); print(f"{lab:44} n={len(v):2}  next-20-tick move against holder {m:+.3f} (t {m/(s/math.sqrt(len(v))):+.1f})")
for kind in ('taken', 'declined', 'auction'):
    for lab, sel in (('pre-trend against (> +0.02)', lambda r: r['pre'] > 0.02), ('pre-trend flat (|.|<=0.02)', lambda r: abs(r['pre']) <= 0.02), ('pre-trend in our favour (< -0.02)', lambda r: r['pre'] < -0.02)):
        show(f"{kind:9} {lab}", [r['post'] for r in rows_out if r['kind'] == kind and sel(r)])
# regression post ~ a + b*pre for fixed tenders (taken+declined)
fx = [r for r in rows_out if r['kind'] != 'auction']
x = [r['pre'] for r in fx]; y = [r['post'] for r in fx]
mx, my = st.mean(x), st.mean(y); b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sum((a - mx) ** 2 for a in x)
a = my - b * mx; res = [c - a - b * xx for xx, c in zip(x, y)]; se_a = st.stdev(res) / math.sqrt(len(x))
print(f"fixed tenders: next-20 move = {a:+.3f} (intercept = crowd with NO prior trend, t {a/se_a:+.1f}) + {b:+.2f} x pre-10 trend   n={len(x)}")
