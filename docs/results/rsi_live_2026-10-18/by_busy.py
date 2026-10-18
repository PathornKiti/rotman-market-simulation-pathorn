"""rsi_bot (MOM10, touch accepts) vs default (own accepts, unwound at once) on EVERY recorded heat, split by how busy
the market was: other traders' volume (rivals.py). Busy = top third (>= the 67th percentile)."""
import glob, json, os, sys, statistics as st, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "rsi_hold_2026-10-17")]
import backtest as B
B.RATE = 2500
rows = []
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    heat = os.path.basename(f)[10:25]
    mid, last, ten, nlv, prof, acc = B.load(f)
    if mid["CRZY"][419] is None or nlv is None: continue
    tv, ours, own = {}, 0, set()
    for l in open(f):
        d = json.loads(l)
        if d["k"] == "market":
            for s in d.get("securities") or []:
                tv[s["ticker"]] = max(tv.get(s["ticker"], 0), s.get("total_volume") or 0)
        elif d["k"] == "end" and d.get("tca"):
            ours = sum(abs(x["qty"]) for x in d["tca"])
        elif d["k"] == "tender_decision" and d["accept"] and not str(d.get("reason", "")).startswith("touch edge") \
                and "MODEL" not in str(d.get("reason", "")).upper():
            own.add(d["tid"])
    if not tv or not ours: continue
    a = B.run_heat(mid, last, ten, -9, "NOW", 0.03, only=own)[0]
    b = B.run_heat(mid, last, ten, 0.0, "MOM10", 0.03)[0]
    rows.append(dict(heat=heat, other=sum(tv.values()) - ours, d=a, r=b))
def T(x): return st.mean(x) / (st.stdev(x) / math.sqrt(len(x)))
cut = sorted(r["other"] for r in rows)[2 * len(rows) // 3]
lo = sorted(r["other"] for r in rows)[len(rows) // 3]
print(f"{len(rows)} heats; other traders' volume: median {st.median(r['other'] for r in rows)/1e6:.2f}M, busy cut {cut/1e6:.2f}M")
for lab, g in (("quiet third", [r for r in rows if r["other"] < lo]), ("middle third", [r for r in rows if lo <= r["other"] < cut]),
               ("busy third", [r for r in rows if r["other"] >= cut]), ("10-17 22:00 on (~6 people)", [r for r in rows if r["heat"] >= "20261017-220000"])):
    x = [r["r"] - r["d"] for r in g]
    print(f"{lab:28} n {len(g):3}  others {st.mean(r['other'] for r in g)/1e6:.2f}M  default {st.mean(r['d'] for r in g):>8,.0f}  "
          f"rsi_bot {st.mean(r['r'] for r in g):>8,.0f}  diff {st.mean(x):>+8,.0f} (t {T(x):+.1f}) better {sum(v > 0 for v in x)}/{len(x)}  worst {min(x):+,.0f}")
xs = [r["other"] for r in rows]; ys = [r["r"] - r["d"] for r in rows]
mx, my = st.mean(xs), st.mean(ys)
print(f"corr(others' volume, rsi_bot gain) {sum((a-mx)*(b-my) for a,b in zip(xs,ys))/math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys)):+.2f}")
