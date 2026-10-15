"""T cap (strategy.drift_horizon_ticks) on the 17 real evening journals: replay frozen vs capped, value the flips on
the recorded mid path (path_cf, minus the 0.054 overstatement calibrated on 55 real blocks)."""
import glob, json, re, statistics as st, sys
sys.path.insert(0, '/Users/pathorn.kit/rotman-market-simulation-pathorn/docs/results')
sys.path.insert(0, '/Users/pathorn.kit/rotman-market-simulation-pathorn/docs/results/evening_2026-10-14')
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import nodrift; from journal_replay import replay
from path_cf import cf

OVER = 0.054
CAP = int(sys.argv[1]) if len(sys.argv) > 1 else 20
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + \
    sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
PAT = re.compile(r"t\s*(\d+) TENDER (\d+) (BUY|SELL) (\w+) x[\d.]+ @ (\S+) -> (ACCEPT|decline) \((.*?)\)")


def answers(lines):
    out = {}
    for l in lines:
        m = PAT.match(l)
        if m:
            tick, tid, side, tk, px, ans, why = m.groups()
            pps = re.search(r"profit/share ([+-][\d.]+)", why)
            bid = re.search(r"competitive bid ([\d.]+)", why)
            out[int(tid)] = dict(tick=int(tick), acc=ans == 'ACCEPT', pps=float(pps.group(1)) if pps else None,
                                 bid=float(bid.group(1)) if bid else None, why=why)
    return out


heats, flips, auc, mism = [], [], [], 0
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    real = {r['tid']: r for r in rows if r['k'] == 'tender_decision'}
    a0 = answers(replay(f, {}))
    a1 = answers(replay(f, {"NODRIFT": True}))
    mism += sum(1 for t, a in a0.items() if t in real and a['acc'] != real[t]['accept'] and seen[t]['fixed'])
    d = 0.0
    for tid, s in seen.items():
        if tid not in a0 or tid not in a1:
            continue
        if not s['fixed']:
            if a0[tid]['bid'] and a1[tid]['bid']:
                sg = 1 if s['action'] == 'BUY' else -1
                auc.append((sg * (s['mid'] - a0[tid]['bid']), sg * (s['mid'] - a1[tid]['bid'])))
            continue
        if a0[tid]['acc'] == a1[tid]['acc']:
            continue
        v = (cf(ticks, s, 1000)[1] / s['qty'] + cf(ticks, s, 1500)[1] / s['qty']) / 2 - OVER
        sg = 1 if s['action'] == 'BUY' else -1
        later = [t for t, x in a0.items() if x['acc'] and seen[t]['fixed'] and 0 < x['tick'] - s['tick'] <= 50]
        r = real.get(tid)
        flips.append(dict(real=('ACC' if r and r['accept'] else 'dec') + (f" {r['pps']:+.3f}" if r else ''),
                          ok=bool(r) and r['accept'] == a0[tid]['acc'], heat=f[-12:-6], tid=tid, tick=s['tick'], tk=s['ticker'], qty=s['qty'],
                          edge=sg * (s['mid'] - s['price']), pps0=a0[tid]['pps'], pps1=a1[tid]['pps'],
                          to='ACCEPT' if a1[tid]['acc'] else 'decline', vps=v, usd=v * s['qty'] * (1 if a1[tid]['acc'] else -1),
                          later=later))
        if flips[-1]['ok']:
            d += flips[-1]['usd']
    heats.append((f[-12:-6], d))

print(f"NO DRIFT TERM, 17 real evening heats (frozen replay vs real fixed answers: {mism} mismatches)\n")
print(f"{'heat':7} {'tid':>4} {'tick':>4} {'stk':4} {'size':>5} {'edge':>6} {'value frozen':>12} {'value capped':>12} {'->':8} {'real answer':12} {'counted':7} {'path $/sh':>9} {'$':>8}  next taken <=50 ticks")
for x in flips:
    print(f"{x['heat']:7} {x['tid']:>4} {x['tick']:>4} {x['tk']:4} {x['qty']//1000:>4}k {x['edge']:+6.3f} {x['pps0']:+12.3f} {x['pps1']:+12.3f} {x['to']:8} {x['real']:12} {'yes' if x['ok'] else 'NO':7} {x['vps']:+9.3f} {x['usd']:+8.0f}  {x['later']}")
ds = [d for _, d in heats]
sd = st.stdev(ds)
print(f"\nper heat: mean {st.mean(ds):+,.0f}  sd {sd:,.0f}  t {st.mean(ds) / (sd / len(ds) ** 0.5) if sd else float('nan'):.2f}  "
      f"better {sum(d > 0 for d in ds)} worse {sum(d < 0 for d in ds)} same {sum(d == 0 for d in ds)}  worst {min(ds):+,.0f}")
print("per heat:", "  ".join(f"{h}:{d:+.0f}" for h, d in heats))
if auc:
    sh = [b - a for a, b in auc]
    print(f"\nauction bids ({len(auc)}): distance from the mid, frozen median {st.median(a for a, _ in auc):.3f} -> capped "
          f"{st.median(b for _, b in auc):.3f}; moved closer by median {abs(st.median(sh)):.3f}, max {-min(sh):.3f}")
