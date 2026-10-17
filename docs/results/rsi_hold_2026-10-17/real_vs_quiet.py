"""Where do real (multi-trader) heats lose vs quiet ones? Per heat: fixed tenders seen / taken, the bot's expected
value of what it took (the draw), what blocks made, the mid move against during unwinds, and declined tenders'
hindsight (mid 50 ticks later vs the tender price, before unwind cost). Groups: quiet = no crowd alarm, crowded."""
import glob, json, os, statistics as st, math, sys
sys.path.insert(0, "docs/results/rsi_hold_2026-10-17")
from backtest import load
def t_(x): return st.mean(x) / (st.stdev(x) / math.sqrt(len(x))) if len(x) > 1 and st.stdev(x) else float("nan")
rows = []
for f in sorted(glob.glob("logs/data/liability-202610*.jsonl")):
    base = os.path.basename(f)[:-6]
    if not os.path.exists(f"logs/{base}.log"): continue
    recs = [json.loads(l) for l in open(f)]
    summ = next((r for r in recs if r["k"] == "summary"), None)
    if not summ: continue
    log = open(f"logs/{base}.log", errors="replace").read()
    seen = [r for r in recs if r["k"] == "tender_seen" and r.get("fixed")]
    dec = {r["tid"]: r for r in recs if r["k"] == "tender_decision"}
    blocks = [r for r in recs if r["k"] == "block_done"]
    mid, last, *_ = load(f)
    fn = []
    for s in seen:
        d = dec.get(s["tid"])
        if d and not d.get("accept") and s.get("price") and s["tick"] + 50 < 420:
            side = 1 if s["action"] == "BUY" else -1
            fn.append(side * (mid[s["ticker"]][s["tick"] + 50] - s["price"]) * s["qty"])
    rows.append(dict(name=base[10:25], crowded="-> CROWDED" in log, nlv=summ["nlv"], seen=len(seen),
                     taken=sum(1 for s in seen if dec.get(s["tid"], {}).get("accept")),
                     exp=sum(b["expected"] for b in blocks), made=sum(b["made"] for b in blocks),
                     sh=sum(b["qty"] for b in blocks),
                     against=sum(b["mid_move"] * b["qty"] for b in blocks if b.get("mid_move") is not None),
                     fn=sum(fn), nfn=len(fn)))
for lab, g in (("quiet (no crowd alarm)", [r for r in rows if not r["crowded"]]),
               ("crowded (alarm on)", [r for r in rows if r["crowded"]])):
    m = lambda k: st.mean(r[k] for r in g)
    print(f"{lab:24} heats {len(g):3}  NLV {m('nlv'):>8,.0f}  fixed seen {m('seen'):.1f} taken {m('taken'):.1f}  "
          f"expected {m('exp'):>8,.0f}  made {m('made'):>8,.0f} ({sum(r['made'] for r in g)/sum(r['exp'] for r in g):.0%})  "
          f"mid move during unwinds {sum(r['against'] for r in g)/sum(r['sh'] for r in g):+.3f}/sh  "
          f"declined: {m('nfn'):.1f}/heat, at +50 ticks {m('fn'):>+8,.0f}/heat")
q = [r for r in rows if not r["crowded"]]; c = [r for r in rows if r["crowded"]]
gap = st.mean(r['nlv'] for r in q) - st.mean(r['nlv'] for r in c)
print(f"\ngap quiet - crowded: {gap:,.0f}/heat; of it: fewer/worse tenders taken (expected) "
      f"{st.mean(r['exp'] for r in q) - st.mean(r['exp'] for r in c):,.0f}, "
      f"execution (made - expected) {st.mean(r['made']-r['exp'] for r in q) - st.mean(r['made']-r['exp'] for r in c):,.0f}")
