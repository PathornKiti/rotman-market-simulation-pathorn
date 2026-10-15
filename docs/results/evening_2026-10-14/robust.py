"""Why b19e95c never went negative in the evening: per heat P&L path, blocks, entry filter, exits, exposure."""
import json, glob, statistics as st, math, sys
sys.path.insert(0, '/private/tmp/claude-502/-Users-pathorn-kit-rotman-market-simulation-pathorn/fe7b4efe-2c77-4101-a354-d8f4f4a44683/scratchpad')
from path_cf import cf
OVER = 0.054
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
H = []
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    stamp = f[-12:-6]
    ver = 'a731c81' if stamp >= '221500' else 'b19e95c'
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    dec = {r['tid']: r for r in rows if r['k'] == 'tender_decision'}
    blocks = [r for r in rows if r['k'] == 'block_done']
    nlv = [ticks[t]['nlv'] for t in sorted(ticks) if t >= 5 and ticks[t].get('nlv') is not None]   # tick < 5: RIT still shows the last heat
    final = next(r for r in rows if r['k'] == 'summary')['nlv']
    gross = [sum(abs(v) for v in ticks[t]['pos'].values()) for t in ticks]
    one = [max(abs(v) for v in ticks[t]['pos'].values()) for t in ticks]
    # declined fixed tenders: what they would have made (path, minus the overstatement)
    saved = []
    for tid, s in seen.items():
        if s['fixed'] and tid in dec and not dec[tid]['accept'] and s['price'] is not None:
            v = cf(ticks, s, 1000)[1] / s['qty'] / 2 + cf(ticks, s, 1500)[1] / s['qty'] / 2 - OVER
            saved.append(v * s['qty'])
    H.append(dict(heat=stamp, ver=ver, final=final, low=min(nlv), high=max(nlv), blocks=blocks,
                  fx=sum(1 for s in seen.values() if s['fixed']), taken=sum(1 for t, s in seen.items() if s['fixed'] and dec.get(t, {}).get('accept')),
                  declined_value=sum(saved), n_decl=len(saved), max_one=max(one), max_gross=max(gross)))
for ver in ('b19e95c', 'a731c81'):
    hs = [h for h in H if h['ver'] == ver]
    print(f"===== {ver}: {len(hs)} evening heats")
    print(f"{'heat':6} {'final':>8} {'lowest':>8} {'highest':>8} {'fixed':>5} {'taken':>5} {'blocks +/-':>10} {'worst blk':>9} {'declined would make':>20} {'max 1 stock':>11}")
    for h in hs:
        b = h['blocks']; w = min((x['made'] for x in b), default=0)
        print(f"{h['heat']:6} {h['final']:+8.0f} {h['low']:+8.0f} {h['high']:+8.0f} {h['fx']:5} {h['taken']:5} {sum(x['made']>0 for x in b):4}/{sum(x['made']<=0 for x in b):<5} {w:+9.0f} {h['declined_value']:+12.0f} ({h['n_decl']:2}) {h['max_one']:11,.0f}")
    B = [x for h in hs for x in h['blocks']]
    made = [x['made'] for x in B]
    print(f"blocks {len(B)}: mean {st.mean(made):+,.0f}  sd {st.stdev(made):,.0f}  losers {sum(m<0 for m in made)}  worst {min(made):+,.0f}  "
          f"| per heat: final mean {st.mean(h['final'] for h in hs):+,.0f} min {min(h['final'] for h in hs):+,.0f}  lowest point mean {st.mean(h['low'] for h in hs):+,.0f}")
    bad = [x for x in B if x['mid_move'] < -0.20]
    if bad:
        print(f"blocks where the price ran > 0.20 against: {len(bad)}, mean {st.mean(x['secs'] for x in bad):.0f} s, crossed {st.mean(x['crossed']/x['qty'] for x in bad):.0%}, "
              f"made mean {st.mean(x['made'] for x in bad):+,.0f}, worst {min(x['made'] for x in bad):+,.0f}")
    print(f"declined tenders would have made in total {sum(h['declined_value'] for h in hs)/1e3:+,.0f}k ({sum(h['n_decl'] for h in hs)} tenders)")
