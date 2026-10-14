"""
Paired A/B on the simulator (t = mean paired difference / its SE, arm 0 = baseline):

    PYTHONPATH=src python scripts/ab.py GROUPS FIRST LAST '{arm0 overrides}' '{arm1}' ...
    PYTHONPATH=src python scripts/ab.py practice,trend,impact,hostile 1 16 '{}' '{"strategy.drift_horizon_ticks":20}'
"""
import json
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, "src")
from ritc.stress import SCENARIOS, run_bot


def job(a):
    name, seed, ov = a
    try:
        return run_bot(seed, name, ov)["score"]
    except Exception:
        return float("nan")

if __name__ == "__main__":
    groups, lo, hi = sys.argv[1].split(","), int(sys.argv[2]), int(sys.argv[3])
    arms = [json.loads(x) for x in sys.argv[4:]]
    names = [n for n, v in SCENARIOS.items() if v[4] in groups or n in groups]
    seeds = range(lo, hi + 1)
    jobs = [(n, s, ov) for ov in arms for n in names for s in seeds]
    with ProcessPoolExecutor() as ex:
        res = list(ex.map(job, jobs, chunksize=1))
    k = len(names) * len(seeds)
    R = [res[i * k:(i + 1) * k] for i in range(len(arms))]
    def t(d):
        return st.fmean(d) / (st.stdev(d) / len(d) ** .5) if len(d) > 1 and st.stdev(d) else 0.0
    print(f"{'scenario':<28}" + "".join(f"{'arm'+str(i):>10}" for i in range(len(arms)))
          + "".join(f"{'d'+str(i):>9}{'t':>6}{'worst':>9}" for i in range(1, len(arms))))
    for j, n in enumerate(names):
        sl = slice(j * len(seeds), (j + 1) * len(seeds))
        row = f"{n:<28}" + "".join(f"{st.fmean(r[sl]):>10,.0f}" for r in R)
        for i in range(1, len(arms)):
            d = [b - a for a, b in zip(R[0][sl], R[i][sl])]
            row += f"{st.fmean(d):>9,.0f}{t(d):>6.1f}{min(R[i][sl]):>9,.0f}"
        print(row + f"   (arm0 worst {min(R[0][sl]):,.0f})")
    row = f"{'POOLED':<28}" + "".join(f"{st.fmean(r):>10,.0f}" for r in R)
    for i in range(1, len(arms)):
        d = [b - a for a, b in zip(R[0], R[i])]
        row += f"{st.fmean(d):>9,.0f}{t(d):>6.1f}{min(R[i]):>9,.0f}"
    print(row)
