import json, glob, statistics as st, math
exec(open(__file__.replace('auctions_trend.py', 'auctions.py')).read().split('print(f"{len(A)}')[0])
# add the 10-tick trend before listing
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    mid = lambda t, tk: (ticks[t]['bid'][tk] + ticks[t]['ask'][tk]) / 2 if t in ticks and ticks[t]['bid'][tk] else None
    for a in A:
        if a['heat'] != f[-11:-6]: continue
        s = next(r for r in rows if r['k'] == 'tender_seen' and r['tid'] == a['tid'])
        sg = 1 if s['action'] == 'BUY' else -1
        m0, mp = mid(s['tick'], a['tk']), mid(s['tick'] - 10, a['tk'])
        a['trend'] = None if None in (m0, mp) else sg * (m0 - mp)        # + = the price was moving our way already
for lab, sel in (('trend our way (> +0.02)', lambda a: a['trend'] > 0.02), ('flat', lambda a: abs(a['trend']) <= 0.02), ('trend against (< -0.02)', lambda a: a['trend'] < -0.02)):
    s = [a for a in A if a.get('trend') is not None and a['move'] is not None and sel(a)]
    vals = {x: st.mean(x + a['move'] - FEE - OVER for a in s) for x in (0.0, 0.05, 0.10, 0.15)}
    print(f"{lab:26} n={len(s):2}  value of a win at x = 0 / 0.05 / 0.10 / 0.15 from the mid: " + " / ".join(f"{v:+.3f}" for v in vals.values()))
print()
for thr in (0.0, 0.02, 0.05):
    for R in ('both',):
        s = [a for a in A if a.get('trend') is not None and a['move'] is not None and a['trend'] > thr]
        v = [0.03 + a['move'] - FEE - OVER for a in s]
        m, sd = st.mean(v), st.stdev(v)
        by = {}
        for a, y in zip(s, v): by.setdefault(a['heat'], []).append(y)
        print(f"trend our way > {thr:.2f}: n={len(v)} auctions in {len(by)} heats, win at 0.03 from the mid -> {m:+.3f}/sh (t {m/(sd/math.sqrt(len(v))):+.1f}), "
              f"positive {sum(y>0 for y in v)}/{len(v)}, per stock " + ", ".join(f"{tk} {st.mean(0.03 + a['move'] - FEE - OVER for a in s if a['tk']==tk):+.2f}" for tk in ('CRZY','TAME','CROC') if any(a['tk']==tk for a in s)))
