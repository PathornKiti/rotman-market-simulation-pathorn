"""Every auction in the 17 evening journals: type, our bid's distance from the mid, and what a block won at distance x
would have made on the recorded path (awarded at expiry, unwound at 1,000-1,500 sh/tick, minus the 0.054/sh the path
overstates real blocks)."""
import json, glob, statistics as st, math, collections
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
FEE, OVER = 0.02, 0.054
A = []
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    mid = lambda t, tk: (ticks[t]['bid'][tk] + ticks[t]['ask'][tk]) / 2 if t in ticks and ticks[t]['bid'][tk] else None
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    dec = {r['tid']: r for r in rows if r['k'] == 'tender_decision'}
    ans = {r['tid']: r for r in rows if r['k'] == 'tender_answer'}
    summ = next((r for r in rows if r['k'] == 'summary'), {})
    for tid, s in seen.items():
        if s['fixed'] or tid not in dec: continue
        d = dec[tid]; tk = s['ticker']; sg = 1 if s['action'] == 'BUY' else -1
        typ = 'WTA' if 'Winner' in (s.get('caption') or '') else ('reserve' if 'Reserve' in (s.get('caption') or '') else '?')
        m0 = mid(s['tick'], tk); te = s['expires']; me = mid(min(te, 419), tk)
        bid = d.get('price')
        dist = sg * (m0 - bid) if (d['accept'] and bid) else None
        out = {}
        for R in (1000, 1500):
            n = math.ceil(s['qty'] / R)
            ms = [mid(t, tk) for t in range(te + 1, min(te + 1 + n, 420))]
            ms = [m for m in ms if m is not None]
            out[R] = sg * (st.mean(ms) - m0) if ms else None
        move = None if None in out.values() else (out[1000] + out[1500]) / 2      # path move vs the LISTING mid
        A.append(dict(heat=f[-11:-6], tid=tid, tk=tk, typ=typ, side=s['action'], q=s['qty'], tick=s['tick'], dist=dist,
                      move=move, pre=None if me is None else sg * (me - m0), won=bool(summ.get('tenders', {}).get(str(tid), {}).get('won')),
                      bid=d['accept'], reason=d['reason'][:40]))
print(f"{len(A)} auctions, won {sum(a['won'] for a in A)}; bid on {sum(a['bid'] for a in A)}; types {collections.Counter(a['typ'] for a in A)}")
for typ in ('WTA', 'reserve'):
    s = [a for a in A if a['typ'] == typ and a['dist'] is not None]
    ds = sorted(a['dist'] for a in s)
    print(f"{typ:8} n={len(s):2} our bid's distance from the mid: median {st.median(ds):.3f}, min {ds[0]:.3f}, 25% {ds[len(ds)//4]:.3f}, max {ds[-1]:.3f} -> all lost")
mv = [a['move'] for a in A if a['move'] is not None]
m, sd = st.mean(mv), st.stdev(mv)
print(f"path move vs the listing mid over the unwind after expiry: {m:+.3f}/sh (t {m/(sd/math.sqrt(len(mv))):+.1f}, n {len(mv)}, + = our way)")
print("value of winning at distance x from the listing mid (x + move - fee - 0.054), mean over all auctions:")
for x in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30):
    v = [x + a['move'] - FEE - OVER for a in A if a['move'] is not None]
    print(f"  x = {x:.2f}: {st.mean(v):+.3f}/sh  (~${st.mean(v)*55000/1e3:+.1f}k per 55k block; {sum(y > 0 for y in v)}/{len(v)} positive)")
mv_mid = [a['pre'] for a in A if a['pre'] is not None]
print(f"mid move listing -> expiry (+ = our way): {st.mean(mv_mid):+.3f}")
print()
print("closest bids we still lost (distance from the listing mid):")
for a in sorted([a for a in A if a['dist'] is not None], key=lambda a: a['dist'])[:8]:
    print(f"  {a['heat']} #{a['tid']} {a['typ']:7} {a['side']:4} {a['tk']} {a['q']/1e3:.0f}k  bid {a['dist']:.3f} from the mid -> lost")
# conditional on the trend before the auction: does a 'trend in our favour' auction pay at a closer bid?
for f in FILES:
    pass
