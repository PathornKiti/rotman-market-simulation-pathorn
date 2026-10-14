"""Real-data check of RIDE on the 17 evening journals: when the replay says 'ride', what happened next on the real path?
Estimated effect per ride span: the real fills in the span (position change) would fill 1 tick better only as far as time &
sales printed at or through the ride price; the rest is sold at the touch when ride turns off."""
import json, glob, re, statistics as st, math
S = '/private/tmp/claude-502/-Users-pathorn-kit-rotman-market-simulation-pathorn/fe7b4efe-2c77-4101-a354-d8f4f4a44683/scratchpad/ride'
FILES = sorted(glob.glob('/Users/pathorn.kit/Downloads/log_transfer/*.jsonl')) + sorted(glob.glob('/Users/pathorn.kit/rotman-market-simulation-pathorn/logs/data/*.jsonl'))
tot_gain, spans, moves5, ride_ticks = 0.0, 0, [], 0
per_heat = []
for f in FILES:
    n = f.split('/')[-1][:-6]
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}
    tas = {}
    for r in rows:
        if r['k'] == 'market':
            for tk, lst in (r.get('tas') or {}).items():
                for x in lst:
                    tas.setdefault((tk, int(x['tick'])), {})[x['id']] = x
    on = {}
    ev = []
    for l in open(f'{S}/{n}.txt'):
        m = re.match(r't\s*(\d+) RIDE (\w+): .* on our ([+-]\d+) block', l)
        if m: on[m.group(2)] = (int(m.group(1)), int(m.group(3))); continue
        m = re.match(r't\s*(\d+) RIDE (\w+) off', l)
        if m and m.group(2) in on:
            t0, held = on.pop(m.group(2)); ev.append((m.group(2), t0, max(int(m.group(1)), t0 + 1), held))
    g_heat = 0.0
    for tk, t0, t1, held in ev:
        if t0 not in ticks or t1 not in ticks: continue
        sell = held > 0
        real = abs(ticks[t1]['pos'][tk] - ticks[t0]['pos'][tk])          # what we really filled while riding
        through = 0.0
        for t in range(t0, t1):
            r = ticks[t]; touch = r['ask'][tk] if sell else r['bid'][tk]
            ride_px = touch + 0.01 if sell else touch - 0.01
            through += sum(x['quantity'] for x in tas.get((tk, t + 1), {}).values()
                           if (x['price'] >= ride_px - 1e-9 if sell else x['price'] <= ride_px + 1e-9))
        filled = min(real, through)
        touch0 = st.mean((ticks[t]['ask'][tk] if sell else ticks[t]['bid'][tk]) for t in range(t0, t1))
        touch1 = ticks[t1]['ask'][tk] if sell else ticks[t1]['bid'][tk]
        later = (touch1 - touch0) if sell else (touch0 - touch1)               # + = selling the rest later is better
        gain = filled * 0.01 + (real - filled) * later
        g_heat += gain; spans += 1; ride_ticks += t1 - t0
        m5 = ticks.get(t0 + 5)
        if m5:
            mid0 = (ticks[t0]['bid'][tk] + ticks[t0]['ask'][tk]) / 2; mid5 = (m5['bid'][tk] + m5['ask'][tk]) / 2
            moves5.append((mid5 - mid0) if sell else (mid0 - mid5))
    per_heat.append(g_heat); tot_gain += g_heat
m, s = st.mean(moves5), st.stdev(moves5)
print(f"ride spans {spans}, {ride_ticks} ticks riding in 17 heats ({ride_ticks/17:.1f} ticks/heat)")
print(f"real mid move our way 5 ticks after ride turns on: {m:+.4f} (t {m/(s/math.sqrt(len(moves5))):+.1f}, n {len(moves5)})")
print(f"estimated effect: {tot_gain:+,.0f} $ total, {tot_gain/17:+,.0f} $/heat; per heat {[round(x) for x in per_heat]}")
