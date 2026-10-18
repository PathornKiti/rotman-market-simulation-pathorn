"""How busy was each heat? Other traders' volume = market total_volume at the bell minus our own fills (all 3 stocks),
plus fines from the RIT trader record. Solo heats (only the market makers) give the floor."""
import glob, json, os, statistics as st, sys
HERE = os.path.dirname(os.path.abspath(__file__))
cr = set(open(os.path.join(HERE, "..", "rsi_hold_2026-10-17", "crowded_heats.txt")).read().split())
rows = []
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    heat = os.path.basename(f)[10:25]
    tv, ours, fines, nlv, prof, alarm = {}, 0, 0.0, None, None, False
    for l in open(f):
        d = json.loads(l)
        k = d["k"]
        if k == "market":
            for s in d.get("securities") or []:
                tv[s["ticker"]] = max(tv.get(s["ticker"], 0), s.get("total_volume") or 0)
            fines = max(fines, (d.get("trader") or {}).get("total_fines") or 0)
        elif k == "end" and d.get("tca"):
            ours = sum(abs(x["qty"]) for x in d["tca"])
        elif k == "summary":
            nlv, prof, alarm = d["nlv"], d.get("profile"), bool((d.get("crowd") or {}).get("crowded"))
    if nlv is None or not tv: continue
    rows.append(dict(heat=heat, prof=prof, nlv=nlv, mkt=sum(tv.values()), ours=ours, other=sum(tv.values()) - ours,
                     fines=fines, alarm=alarm or heat in cr))
for r in rows:
    print(f"{r['heat']} {str(r['prof'])[:12]:12} NLV {r['nlv']:>9,.0f}  market {r['mkt']/1e3:>7,.0f}k  ours {r['ours']/1e3:>5,.0f}k  others {r['other']/1e3:>7,.0f}k  fines {r['fines']:>7,.0f} {'ALARM' if r['alarm'] else ''}")
