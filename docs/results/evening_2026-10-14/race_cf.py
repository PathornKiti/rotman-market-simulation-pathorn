"""RACE counterfactual on the recorded books: from the tick after acceptance, cross every tick, taking `part` of the
visible depth within `slip` of the touch (fresh book each tick = optimistic about refills, `part` < 1 offsets it).
Compare with what the block really made. 17 evening heats, single-tender fixed blocks."""
import json, glob, sys, statistics as st, math
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
FEE = 0.02
def race(books_by_tick, t1, tk, q, sell, slip, part):
    left, cash, t = q, 0.0, t1
    while left > 0 and t < 420:
        bk = books_by_tick.get(t, {}).get(tk)
        t += 1
        if not bk: continue
        lv = bk['bids'] if sell else bk['asks']
        lv = [(o['price'], o['quantity'] - (o.get('quantity_filled') or 0)) for o in lv if o.get('trader_id') == 'ANON']
        if not lv: continue
        touch = lv[0][0]
        avail = [(p, v) for p, v in lv if abs(p - touch) <= slip + 1e-9]
        take = min(left, part * sum(v for _, v in avail))
        got = 0.0
        for p, v in avail:
            x = min(v * part, take - got)
            if x <= 0: break
            cash += x * p; got += x
        left -= got
    return cash, q - left, t - t1
out = []
for f in FILES:
    rows = [json.loads(l) for l in open(f)]
    books = {}
    for r in rows:
        if r['k'] == 'market': books[r['case']['tick']] = r['books']
    seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    for b in (r for r in rows if r['k'] == 'block_done' and len(r['tenders']) == 1):
        s = seen[b['tenders'][0]]
        if s['price'] is None: continue
        sell = s['action'] == 'BUY'                       # BUY tender -> we hold long -> sell to unwind
        q = b['qty']; t1 = b['start_tick']
        row = dict(heat=f[-11:-6], tk=s['ticker'], made=b['made'], secs=b['secs'], crossed=b['crossed'] / q)
        for slip, part in ((0.04, 0.35), (0.04, 0.7), (0.10, 0.5)):
            cash, done, n = race(books, t1, s['ticker'], q, sell, slip, part)
            if done < q * 0.999: row[(slip, part)] = None; continue
            pnl = (cash - s['price'] * q) if sell else (s['price'] * q - cash)
            row[(slip, part)] = (pnl - FEE * q, n)
        out.append(row)
keys = [(0.04, 0.35), (0.04, 0.7), (0.10, 0.5)]
for tk in ('CRZY', 'TAME', 'CROC', None):
    s = [o for o in out if (tk is None or o['tk'] == tk) and all(o[k] for k in keys)]
    line = f"{tk or 'ALL':4} n={len(s):2} real made {sum(o['made'] for o in s)/1e3:+7.1f}k"
    for k in keys:
        v = [o[k][0] - o['made'] for o in s]
        line += f" | race slip {k[0]:.2f} part {k[1]:.2f}: {sum(o[k][0] for o in s)/1e3:+7.1f}k (diff {sum(v)/1e3:+6.1f}k, t {st.mean(v)/(st.stdev(v)/math.sqrt(len(v))):+.1f}, wins {sum(x>0 for x in v)}/{len(v)}, ~{st.mean(o[k][1] for o in s):.0f} ticks)"
    print(line)
print()
for o in out:
    if o['tk'] != 'CROC' and o[keys[0]]:
        print(o['heat'], o['tk'], f"real {o['made']:+8.0f} ({o['secs']}s, crossed {o['crossed']:.0%})  race {o[keys[0]][0]:+8.0f} in {o[keys[0]][1]} ticks")
