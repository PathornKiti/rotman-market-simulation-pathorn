# path estimate vs live realized, every taken fixed tender (before vs after 16:49 on 10-15). Run from the repo root: PYTHONPATH=src .venv/bin/python docs/results/screening_2026-10-15/calib.py
import glob, json, os, sys, statistics as st
ROOT=os.getcwd(); sys.path[:0]=[f"{ROOT}/docs/results", f"{ROOT}/docs/results/evening_2026-10-14", f"{ROOT}/src"]
from path_cf import cf
out=[]
for f in sorted(glob.glob("logs/data/*.jsonl")):
    rows=[json.loads(l) for l in open(f)]
    summ=next((r for r in rows if r["k"]=="summary"),None)
    if not summ or not any(r["k"]=="market" for r in rows): continue
    ticks={r["tick"]:r for r in rows if r["k"]=="tick"}
    seen={r["tid"]:r for r in rows if r["k"]=="tender_seen"}
    h=os.path.basename(f)[10:25]
    for tid,t in summ["tenders"].items():
        s=seen.get(int(tid))
        if t["kind"]!="fixed" or not t["won"] or t["realized"] is None or not s: continue
        v=(cf(ticks,s,1000)[1]/s["qty"]+cf(ticks,s,1500)[1]/s["qty"])/2
        out.append((h, tid, t["ticker"], v, t["realized"]/t["qty"], t["vs_mid"]))
for lab,sel in (("old (<16:49)",lambda h:h<"20261015-1649"),("new 18",lambda h:h>="20261015-1649")):
    xs=[o for o in out if sel(o[0])]
    d=[o[3]-o[4] for o in xs]
    print(f"{lab}: n {len(xs)} path-minus-live mean {st.mean(d):+.3f}/sh sd {st.stdev(d):.3f} t {st.mean(d)/(st.stdev(d)/len(d)**.5):+.2f}  live mean {st.mean(o[4] for o in xs):+.3f}/sh")
    for tk in ("CRZY","TAME","CROC"):
        y=[o for o in xs if o[2]==tk]; dd=[o[3]-o[4] for o in y]
        print(f"   {tk} n {len(y)} path-live {st.mean(dd):+.3f}  live {st.mean(o[4] for o in y):+.3f}/sh  live edge<0.15: {st.mean([o[4] for o in y if o[5]<0.15] or [0]):+.3f} (n {sum(o[5]<0.15 for o in y)})")
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "calib.json"), "w"))
