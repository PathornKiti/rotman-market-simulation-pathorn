"""Pair two `ritc stress --out` runs seed by seed: mean difference, t, worst heat per scenario, pooled."""
import json, statistics as st, sys

a, b = (json.load(open(p))["runs"] for p in sys.argv[1:3])
A = {(r[1], r[2]): r[3]["score"] for r in a if r[0] == "bot"}
B = {(r[1], r[2]): r[3]["score"] for r in b if r[0] == "bot"}
scen = list(dict.fromkeys(k[0] for k in A))
print(f"{'scenario':30} {'frozen':>9} {'T cap 20':>9} {'diff':>8} {'t':>5} {'worst0':>9} {'worst1':>9}  better/worse")
pool, rival = [], []
for s in scen:
    ks = sorted(k for k in A if k[0] == s and k in B)
    d = [B[k] - A[k] for k in ks]
    pool += d
    if any(w in s for w in ("rival", "hostile", "anchored", "crowd")):
        rival += d
    sd = st.stdev(d) if len(d) > 1 else 0
    t = st.mean(d) / (sd / len(d) ** 0.5) if sd else 0.0
    print(f"{s:30} {st.mean(A[k] for k in ks):9,.0f} {st.mean(B[k] for k in ks):9,.0f} {st.mean(d):+8,.0f} {t:5.1f} "
          f"{min(A[k] for k in ks):9,.0f} {min(B[k] for k in ks):9,.0f}  {sum(x > 0 for x in d)}/{sum(x < 0 for x in d)}")
for lab, d in (("POOLED all", pool), ("POOLED rival/hostile", rival)):
    sd = st.stdev(d)
    print(f"{lab:30} n={len(d)} mean {st.mean(d):+,.0f} t {st.mean(d) / (sd / len(d) ** 0.5):.2f} "
          f"better {sum(x > 0 for x in d)} worse {sum(x < 0 for x in d)}")
