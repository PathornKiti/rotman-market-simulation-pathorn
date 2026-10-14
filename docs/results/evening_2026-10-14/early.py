import json, glob, statistics as st, math, sys
sys.path.insert(0, '/private/tmp/claude-502/-Users-pathorn-kit-rotman-market-simulation-pathorn/fe7b4efe-2c77-4101-a354-d8f4f4a44683/scratchpad')
from path_cf import cf
S = '/private/tmp/claude-502/-Users-pathorn-kit-rotman-market-simulation-pathorn/fe7b4efe-2c77-4101-a354-d8f4f4a44683/scratchpad/split'
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
R = []
for f in FILES:
    n = f.split('/')[-1][:-6]
    parts = {int(k): v for k, v in json.load(open(f'{S}/{n}.json')).items()}
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    dec = {r['tid']: r for r in rows if r['k'] == 'tender_decision'}
    made = {b['tenders'][0]: b['made'] for b in rows if b['k'] == 'block_done' and len(b['tenders']) == 1}
    for tid, s in seen.items():
        if not s['fixed'] or tid not in dec or tid not in parts: continue
        p = parts[tid]; d = dec[tid]
        c1000 = cf(ticks, s, 1000)[1] / s['qty']; c1500 = cf(ticks, s, 1500)[1] / s['qty']
        R.append(dict(heat=n[-6:], tid=tid, tick=s['tick'], tk=s['ticker'], edge=p['edge'], pps=d['pps'], acc=d['accept'],
                      walk=p['walk'], drift=p['drift'], risk=p['risk'], crowd=p['crowd'], mode=p['mode'], utick=p['utick'],
                      made=(made.get(tid, None) / s['qty'] if tid in made else None), cf=(c1000 + c1500) / 2, why=d['reason'][:28]))
def summ(lab, s):
    if not s: return
    m = lambda k: st.mean(x[k] for x in s)
    print(f"{lab:30} n={len(s):3} taken {sum(x['acc'] for x in s):3}  edge {m('edge'):+.3f} | charge: walk {m('walk'):.3f} fee 0.020 drift {m('drift'):.3f} risk {m('risk'):.3f} crowd {m('crowd'):.3f} = {m('edge')-m('pps'):.3f} | value {m('pps'):+.3f}"
          f" | path outcome {m('cf'):+.3f}/sh  book-mode {sum(x['mode']=='book' for x in s)}/{len(s)}")
early = [x for x in R if x['tick'] < 100]
late = [x for x in R if x['tick'] >= 100]
print("17 evening heats, fixed tenders ($/share; path outcome = edge + recorded mid move over a 1,000-1,500 sh/tick unwind - fee)")
summ('EARLY (< tick 100) all', early)
summ('  early taken', [x for x in early if x['acc']])
summ('  early declined', [x for x in early if not x['acc']])
summ('  early declined, edge >= 0.15', [x for x in early if not x['acc'] and x['edge'] >= 0.15])
summ('LATER (>= tick 100) all', late)
summ('  later taken', [x for x in late if x['acc']])
summ('  later declined', [x for x in late if not x['acc']])
summ('  later declined, edge >= 0.15', [x for x in late if not x['acc'] and x['edge'] >= 0.15])
print()
print("every early fixed tender:")
for x in early:
    print(f"{x['heat']} #{x['tid']:<4} t{x['tick']:<3} {x['tk']} edge {x['edge']:+.3f} walk {x['walk']:.3f} drift {x['drift']:.3f} risk {x['risk']:.3f} crowd {x['crowd']:.3f} ({x['mode']}, {x['utick']:.0f} ticks) -> value {x['pps']:+.3f} {'TAKEN' if x['acc'] else 'decl '} "
          f"| made {'' if x['made'] is None else format(x['made'], '+.3f'):>6}  path {x['cf']:+.3f}")
# first tender taken per heat
print()
for h in sorted(set(x['heat'] for x in R)):
    t = [x['tick'] for x in R if x['heat'] == h and x['acc']]
    print(h, 'first fixed tender taken at tick', min(t) if t else None, end=' | ')
