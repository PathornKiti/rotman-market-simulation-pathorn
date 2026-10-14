"""Every block in the evening heats: where the tender edge went (mid move against us during the unwind, fills vs mid)."""
import json, glob, statistics as st
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
out = []
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    for b in (r for r in rows if r['k'] == 'block_done'):
        q = b['qty']
        edge = 0.0
        for tid in b['tenders']:
            s = seen[tid]; sg = 1 if s['action'] == 'BUY' else -1
            if s['price'] is not None:
                edge += sg * (s['mid'] - s['price']) * s['qty']
        out.append(dict(heat=f[-11:-6], tk=b['ticker'], q=q, secs=b['secs'], rested=b['rested'], crossed=b['crossed'],
                        edge=edge, move=b['mid_move'] * q, fills=b['fill_edge'] * q, made=b['made'], exp=b['expected'],
                        n=len(b['tenders'])))
print(f"{'heat':6} {'tk':4} {'q':>6} {'secs':>4} {'cross%':>6} {'edge$':>8} {'mid move$':>9} {'fills$':>7} {'fee$':>6} {'made':>8}")
for o in out:
    print(f"{o['heat']:6} {o['tk']:4} {o['q']:6.0f} {o['secs']:4} {o['crossed']/o['q']*100:5.0f}% {o['edge']:+8.0f} {o['move']:+9.0f} {o['fills']:+7.0f} {-0.02*o['q']:+6.0f} {o['made']:+8.0f}")
print()
for tk in ('CRZY', 'TAME', 'CROC', None):
    s = [o for o in out if tk is None or o['tk'] == tk]
    E, M, F, D = (sum(o[k] for o in s) for k in ('edge', 'move', 'fills', 'made'))
    print(f"{tk or 'ALL':4}: {len(s):2} blocks  edge {E/1e3:+7.1f}k  mid move during unwind {M/1e3:+7.1f}k  fills vs mid {F/1e3:+6.1f}k  made {D/1e3:+7.1f}k"
          f"  | blocks where move ate >50% of edge: {sum(o['move'] < -0.5*o['edge'] for o in s)}/{len(s)}")
# split by speed
for lab, sel in (('fast (crossed >= 50%)', lambda o: o['crossed'] >= 0.5*o['q']), ('rested (crossed < 50%)', lambda o: o['crossed'] < 0.5*o['q'])):
    s = [o for o in out if sel(o)]
    print(f"{lab:24}: {len(s)} blocks  edge/sh {sum(o['edge'] for o in s)/sum(o['q'] for o in s):+.3f}  move/sh {sum(o['move'] for o in s)/sum(o['q'] for o in s):+.3f}  fills/sh {sum(o['fills'] for o in s)/sum(o['q'] for o in s):+.3f}  made/sh {sum(o['made'] for o in s)/sum(o['q'] for o in s):+.3f}  mean secs {st.mean(o['secs'] for o in s):.0f}")
