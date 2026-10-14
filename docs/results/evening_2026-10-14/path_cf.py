"""Path counterfactual for fixed tenders: unwind linearly at R shares/tick on the RECORDED mid path.
pnl/share = edge + sg*(mean mid over the unwind - mid at the tender) - fee.  Calibrated against the real 'made'."""
import json, glob, sys, math, statistics as st
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
FEE = 0.02
def load(f):
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    dec = {r['tid']: r for r in rows if r['k'] == 'tender_decision'}
    blocks = [r for r in rows if r['k'] == 'block_done']
    start = next(r for r in rows if r['k'] == 'start')
    return rows, ticks, seen, dec, blocks, start
def mid(ticks, t, tk):
    r = ticks.get(t)
    if not r or not r['bid'][tk] or not r['ask'][tk]:
        return None
    return (r['bid'][tk] + r['ask'][tk]) / 2
def cf(ticks, s, R, lag=1):
    tk, sg, q = s['ticker'], (1 if s['action'] == 'BUY' else -1), s['qty']
    m0 = s['mid']; edge = sg * (m0 - s['price'])
    n = max(1, math.ceil(q / R)); t0 = s['tick'] + lag
    ms = [mid(ticks, min(t, 419), tk) for t in range(t0, t0 + n)]
    ms = [m for m in ms if m is not None]
    return edge, (edge + sg * (st.mean(ms) - m0) - FEE) * q
if __name__ == '__main__':
    tk_sel = sys.argv[1] if len(sys.argv) > 1 else 'CROC'
    print(f"{'heat':7} {'tid':>4} {'side':4} {'tk':4} {'edge':>6} {'model':>7} {'crowd':5} {'answer':7} {'made':>8} | path cf @700 @1000 @1500 sh/tick")
    for f in FILES:
        rows, ticks, seen, dec, blocks, start = load(f)
        made = {}
        for b in blocks:
            if len(b['tenders']) == 1: made[b['tenders'][0]] = b['made']
            else:
                for t in b['tenders']: made[t] = f"({b['made']:+.0f} joint)"
        for tid, s in seen.items():
            if not s['fixed'] or s['ticker'] != tk_sel: continue
            d = dec.get(tid, {})
            e, _ = cf(ticks, s, 1000)
            if e < 0.10: continue
            c = [cf(ticks, s, R)[1] for R in (700, 1000, 1500)]
            mm = made.get(tid, '')
            mm = f"{mm:+8.0f}" if isinstance(mm, (int, float)) else f"{mm:>8}"
            crowd = 'ON' if 'crowd ON' in str(d) else ''
            print(f"{f[-11:-6]:7} {tid:>4} {s['action']:4} {s['ticker']:4} {e:+6.3f} {d.get('pps', float('nan')):+7.3f} {crowd:5} {('ACCEPT' if d.get('accept') else 'decline'):7} {mm} | {c[0]/1e3:+6.1f}k {c[1]/1e3:+6.1f}k {c[2]/1e3:+6.1f}k  {d.get('reason','')[:30]}")
