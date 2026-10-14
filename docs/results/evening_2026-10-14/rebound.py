"""After each block ends: does the mid come back OUR way (we sold the dip) or keep going (we were right to hurry)?"""
import json, glob, statistics as st, math
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
K = (10, 20, 40)
res = {'fast (crossed >=50%)': [], 'rested (<50%)': []}
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    mid = lambda t, tk: (ticks[t]['bid'][tk] + ticks[t]['ask'][tk]) / 2 if t in ticks and ticks[t]['bid'][tk] else None
    for b in (r for r in rows if r['k'] == 'block_done'):
        tk = b['ticker']; w = 1 if b['side'] == 'SELL' else -1     # we were SELLING to unwind a long: + = price up = our way
        e = b['tick']; m0 = mid(e, tk)
        if m0 is None: continue
        mv = [None if mid(e + k, tk) is None else w * (mid(e + k, tk) - m0) for k in K]
        res['fast (crossed >=50%)' if b['crossed'] >= 0.5 * b['qty'] else 'rested (<50%)'].append((mv, b['qty'], b['mid_move']))
print("mid move AFTER the block ended, + = back our way (the price we sold into was a dip), $/share, 17 evening heats")
for lab, L in res.items():
    cells = []
    for i, k in enumerate(K):
        v = [x[0][i] for x in L if x[0][i] is not None]
        m, s = st.mean(v), st.stdev(v); cells.append(f"+{k}: {m:+.3f} (t {m/(s/math.sqrt(len(v))):+.1f}, n {len(v)})")
    print(f"{lab:22} move during the block {st.mean(x[2] for x in L):+.3f}   " + "   ".join(cells))
