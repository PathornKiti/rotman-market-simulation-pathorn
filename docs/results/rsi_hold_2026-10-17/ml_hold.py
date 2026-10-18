"""Hold signal: 10-tick momentum vs the ML price model (models_train_only.json, trained up to 10-15 -> every heat from
10-16 on is out-of-sample). Same accepts (every fixed tender priced better than the touch), gradual exits 2,500 sh/tick."""
import glob, json, os, sys, statistics as st, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "ml_2026-10-16"), os.path.join(HERE, "..", "ml_2026-10-15")]
import backtest as B
from features import journal, predict
M = json.load(open(os.path.join(HERE, "..", "ml_2026-10-16", "models_train_only.json")))
train, pt = set(M["train_heats"]), M["models"]["price_trees"]
crowded = set(open(os.path.join(HERE, "crowded_heats.txt")).read().split())
B.RATE = 2500
rules = ["NOW", "MOM10", "MOMT0.05", "MLT0.00", "MLT0.02", "MLT0.05"]
res = {r: [] for r in rules}; cr = []
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    name = os.path.basename(f)[10:25]
    if name in train: continue
    j = journal(f)
    mid, last, ten, nlv, prof, acc = B.load(f)
    if not j or not j["price"] or mid["CRZY"][419] is None or nlv is None: continue
    B.PRED = {}
    for r in j["price"]:
        B.PRED.setdefault(r["tk"], {})[int(r["tick"])] = predict(pt, r)
    cr.append(name in crowded)
    for r in rules:
        res[r].append(B.run_heat(mid, last, ten, -9 if r == "NOW" else 0.0, r, 0.03, only=acc if r == "NOW" else None)[0])
def T(d): return st.mean(d) / (st.stdev(d) / math.sqrt(len(d)))
print(f"{len(cr)} out-of-sample heats ({sum(cr)} crowded); baseline NOW = the default's accepts, unwound at once")
for r in rules[1:]:
    d = [x - y for x, y in zip(res[r], res["NOW"])]; q = sorted(res[r])
    dq = [x for x, c in zip(d, cr) if not c]; dc = [x for x, c in zip(d, cr) if c]
    print(f"  {r:9} mean {st.mean(res[r]):>7,.0f} median {st.median(res[r]):>7,.0f} worst10% {q[len(q)//10]:>7,.0f} | vs default {st.mean(d):+8,.0f} (t {T(d):+.1f}) "
          f"worse {sum(x<0 for x in d):2} worst {min(d):+8,.0f} | quiet {st.mean(dq):+8,.0f} | crowded {st.mean(dc):+8,.0f} worst {min(dc):+8,.0f}")
m, ml = res["MOM10"], res["MLT0.00"]
d = [b - a for a, b in zip(m, ml)]
print(f"ML (0.00) - momentum, same heats: {st.mean(d):+,.0f}/heat (t {T(d):+.1f}), ML better in {sum(x>0 for x in d)}/{len(d)}")
