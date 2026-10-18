"""Gaps in the live rsi heats (every heat run with trend_hold_ticks > 0):
 1 fixed tenders declined for RISK ROOM (held blocks use the limits) - hindsight value at +50 ticks, 0.03/sh exit cost
 2 TOUCH ACCEPTs (taken only because the price beat the touch) - realized
 3 hold exits - gain at the 'off' tick vs the best gain during the hold (giveback of the momentum exit)
 4 the unwind after a hold - speed and the mid move during the block vs blocks unwound at once
Compared, where it makes sense, with default heats (A_default / K_front_skip / crowd_model) since 10-17 22:00."""
import glob, json, os, statistics as st
def mids(R):
    m = {}
    for r in R:
        if r["k"] == "tick":
            for t in r["bid"]:
                b, a = r["bid"][t], r["ask"][t]
                if b and a: m.setdefault(t, {})[r["tick"]] = (b + a) / 2
    return m
def at(m, t, i):
    for j in range(i, -1, -1):
        if j in m.get(t, {}): return m[t][j]
out = {"rsi": dict(heats=0, room=[], touch=[], give=[], held_blk=[], now_blk=[]),
       "default": dict(heats=0, room=[], touch=[], give=[], held_blk=[], now_blk=[])}
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    heat = os.path.basename(f)[10:25]
    if heat < "20261017-220000": continue
    R = [json.loads(l) for l in open(f)]
    s = next((r for r in R if r["k"] == "summary"), None)
    if not s or not s["nlv"]: continue
    g = out["rsi" if "trend_hold" in os.path.basename(f) else "default"]
    g["heats"] += 1
    m = mids(R)
    seen = {r["tid"]: r for r in R if r["k"] == "tender_seen"}
    ten = s.get("tenders") or {}
    for d in (r for r in R if r["k"] == "tender_decision"):
        x = seen.get(d["tid"]) or {}
        if not d["accept"] and d["reason"].startswith("exceeds risk room") and x.get("fixed") and x.get("price"):
            sd = 1 if x["action"] == "BUY" else -1
            fut = at(m, x["ticker"], min(d["tick"] + 50, 419))
            held = {k: v for k, v in (next((r for r in R if r["k"] == "tick" and r["tick"] >= d["tick"]), {}).get("pos") or {}).items() if v}
            g["room"].append((heat, x["ticker"], x["action"], x["qty"], round(sd * (x["bid" if sd > 0 else "ask"] - x["price"]), 3),
                              x["qty"] * (sd * (fut - x["price"]) - 0.03), held))
        if d["accept"] and d["reason"].startswith("touch edge"):
            g["touch"].append((heat, (ten.get(str(d["tid"])) or {}).get("realized") or 0))
    for h in (r for r in R if r["k"] == "trend_hold" and r["ticks"] > 0):
        sd, t0 = (1 if h["held"] > 0 else -1), h["tick"] - h["ticks"]
        m0 = at(m, h["ticker"], t0)
        best = max(sd * (at(m, h["ticker"], j) - m0) for j in range(t0, h["tick"] + 1))
        g["give"].append((heat, h["ticker"], h["ticks"], round(best, 3), round(h["gained"], 3), abs(h["held"])))
    offs = {(h["ticker"], h["tick"]) for h in R if h["k"] == "trend_hold" and h["ticks"] > 0}
    for b in s.get("blocks") or []:
        key = "held_blk" if any(tk == b["ticker"] and abs(tt - b["start_tick"]) <= 3 for tk, tt in offs) else "now_blk"
        g[key].append((b["rate"], b["mid_move"], b["fill_edge"], b["made"]))
for k, g in out.items():
    print(f"=== {k}: {g['heats']} heats since 10-17 22:00")
    r = g["room"]
    print(f" 1 fixed tenders declined for risk room: {len(r)} ({len(r)/max(g['heats'],1):.1f}/heat), hindsight +50 ticks "
          f"total {sum(x[5] for x in r):+,.0f} ({sum(x[5] for x in r)/max(g['heats'],1):+,.0f}/heat), winners {sum(x[5] > 0 for x in r)}")
    for x in r: print(f"     {x[0]} {x[2]:4} {x[1]} {x[3]:>6,.0f} touch edge {x[4]:+.3f} -> {x[5]:+9,.0f}   holding {x[6]}")
    t = g["touch"]
    if t: print(f" 2 touch accepts: {len(t)}, realized {sum(v for _, v in t):+,.0f}, losers {sum(v < 0 for _, v in t)}, worst {min(v for _, v in t):+,.0f}")
    gv = g["give"]
    if gv:
        gb = [x[3] - x[4] for x in gv]
        print(f" 3 holds that ran: {len(gv)}, best gain during hold mean {st.mean(x[3] for x in gv):+.3f}/sh, at the exit "
              f"{st.mean(x[4] for x in gv):+.3f}/sh -> giveback {st.mean(gb):.3f}/sh = ${sum(b * x[5] for b, x in zip(gb, gv)):,.0f} total "
              f"(${sum(b * x[5] for b, x in zip(gb, gv))/g['heats']:,.0f}/heat); exits below 0: {sum(x[4] < 0 for x in gv)}")
    for lab in ("held_blk", "now_blk"):
        b = g[lab]
        if b: print(f" 4 {lab:8} n {len(b):3} rate {st.mean(x[0] for x in b):>6,.0f} sh/s  mid move {st.mean(x[1] for x in b):+.3f} our way  "
                    f"fills {st.mean(x[2] for x in b):+.4f} vs mid  made {st.mean(x[3] for x in b):>+8,.0f}")
