"""Same-heat counterfactual on every heat since --since (default 10-18): what default / rsi_bot / rsi_ml would have made
on that heat's recorded mids (backtest.py, gradual exits 2,500 sh/tick at mid - 0.03). Removes the tender-draw luck that
makes live profile-vs-profile means noisy. ML hold uses the LIVE model (ml_2026-10-16/models.json, trained to 10-16
20:51 -> every heat here is out-of-sample).
  NOW      = the default bot's own accepts (accepted, reason not 'touch edge'/model), unwound at once
  MOM10    = rsi_bot: every fixed tender priced better than the touch, hold while the last 10 ticks went our way
  MLT0.00  = rsi_ml : same accepts, hold while the ML 10-tick prediction is our way
  END      = same accepts, hold to the bell (upper/lower bound on 'never unwind')"""
import glob, json, os, sys, statistics as st, math
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "..")
sys.path[:0] = [os.path.join(R, "rsi_hold_2026-10-17"), os.path.join(R, "ml_2026-10-16"), os.path.join(R, "ml_2026-10-15")]
import backtest as B
from features import journal, predict
M = json.load(open(os.path.join(R, "ml_2026-10-16", "models.json")))
train, pt = set(M["train_heats"]), M["models"]["price_trees"]
since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "20261018-000000"
B.RATE = 2500
rules = ["NOW", "MOM10", "MLT0.00", "END"]
rows = []
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    name = os.path.basename(f)[10:25]
    if name < since or name in train: continue
    j = journal(f)
    mid, last, ten, nlv, prof, acc = B.load(f)
    if not j or not j["price"] or mid["CRZY"][419] is None or nlv is None: continue
    own = set()
    for l in open(f):
        d = json.loads(l)
        if d["k"] == "tender_decision" and d["accept"] and not str(d.get("reason", "")).startswith("touch edge") \
                and "MODEL" not in str(d.get("reason", "")).upper():
            own.add(d["tid"])
    B.PRED = {}
    for r in j["price"]:
        B.PRED.setdefault(r["tk"], {})[int(r["tick"])] = predict(pt, r)
    v = {r: B.run_heat(mid, last, ten, -9 if r == "NOW" else 0.0, r, 0.03, only=own if r == "NOW" else None)[0] for r in rules}
    rows.append(dict(heat=name, prof=prof, live=nlv, **v))
def T(d): return st.mean(d) / (st.stdev(d) / math.sqrt(len(d))) if len(d) > 1 and st.stdev(d) > 0 else float("nan")
print(f"{len(rows)} heats since {since}; replay $ (live NLV for reference)")
print(f"{'heat':16}{'live profile':14}{'live':>9} | {'default':>9} {'rsi_bot':>9} {'rsi_ml':>9} {'hold-end':>9}")
for r in rows:
    print(f"{r['heat']:16}{r['prof']:14}{r['live']:>9,.0f} | {r['NOW']:>9,.0f} {r['MOM10']:>9,.0f} {r['MLT0.00']:>9,.0f} {r['END']:>9,.0f}")
print()
for r_, lab in (("NOW", "default"), ("MOM10", "rsi_bot"), ("MLT0.00", "rsi_ml"), ("END", "hold-end")):
    x = [r[r_] for r in rows]
    print(f"{lab:9} mean {st.mean(x):>9,.0f} median {st.median(x):>9,.0f} worst {min(x):>9,.0f} best {max(x):>9,.0f}  >=70k {sum(v >= 70000 for v in x)}/{len(x)}")
for a, b in (("MOM10", "NOW"), ("MLT0.00", "NOW"), ("MLT0.00", "MOM10")):
    d = [r[a] - r[b] for r in rows]
    print(f"{a} - {b}: {st.mean(d):+,.0f}/heat (t {T(d):+.1f}), better in {sum(x > 0 for x in d)}/{len(d)}, worst {min(d):+,.0f}")
for p in ("rsi_bot", "rsi_ml"):
    d = [(r["live"], r["MOM10" if p == "rsi_bot" else "MLT0.00"]) for r in rows if r["prof"] == p]
    if d: print(f"replay fidelity {p}: live mean {st.mean(x for x, _ in d):,.0f} vs replay {st.mean(y for _, y in d):,.0f} (n {len(d)})")
cr = set(open(os.path.join(R, "rsi_hold_2026-10-17", "crowded_heats.txt")).read().split()) | {"20261018-005317"}
for lab, g in (("quiet", [r for r in rows if r["heat"] not in cr]), ("crowded", [r for r in rows if r["heat"] in cr])):
    if not g: continue
    for a in ("MOM10", "MLT0.00"):
        d = [r[a] - r["NOW"] for r in g]
        print(f"{lab:8} n {len(g):2} {a:8} - default: {st.mean(d):+,.0f}/heat (t {T(d):+.1f}) better {sum(x > 0 for x in d)}/{len(d)} worst {min(d):+,.0f}")
