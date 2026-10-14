import json, re, subprocess, sys, glob
from concurrent.futures import ThreadPoolExecutor
S = '/private/tmp/claude-502/-Users-pathorn-kit-rotman-market-simulation-pathorn/ece9f5a1-0856-4f5f-8ad8-df9a3792289b/scratchpad'
ov = sys.argv[1]
out = {(o['heat'], o['tid']) if 'heat' in o else None: o for o in []}
heats = sorted(glob.glob('logs/data/*.jsonl'))
def run(args):
    f, o = args
    r = subprocess.run(['.venv/bin/python', f'{S}/journal_replay.py', f, o], capture_output=True, text=True, env={'PYTHONPATH': 'src', 'PATH': '/usr/bin:/bin'})
    return {int(m.group(1)): m.group(2) for m in re.finditer(r'TENDER (\d+) .*? -> (ACCEPT|decline)', r.stdout)}
with ThreadPoolExecutor(8) as ex:
    base = list(ex.map(run, [(f, sys.argv[2] if len(sys.argv) > 2 else '{}') for f in heats]))
    new = list(ex.map(run, [(f, ov) for f in heats]))
import statistics as st
for f, b, n in zip(heats, base, new):
    rows = [json.loads(l) for l in open(f)]
    ticks = {r['tick']: r for r in rows if r['k'] == 'tick'}; seen = {r['tid']: r for r in rows if r['k'] == 'tender_seen'}
    for tid in sorted(set(b) | set(n)):
        if b.get(tid) == n.get(tid): continue
        s = seen[tid]; tk = s['ticker']; sg = 1 if s['action'] == 'BUY' else -1
        mid = lambda t: (ticks[t]['bid'][tk] + ticks[t]['ask'][tk]) / 2
        t0 = s['tick']; fut = [mid(t) for t in range(t0 + 1, min(t0 + 26, 420)) if t in ticks]
        cf = ((sg * (s['mid'] - s['price']) if s['price'] else 0) + sg * (st.mean(fut) - s['mid']) - 0.014) * s['qty']
        print(f"{f[-11:-6]} #{tid} {s['action']} {tk}: {b.get(tid)} -> {n.get(tid)}  (real outcome if taken {cf/1e3:+.1f}k)")
print('done')
