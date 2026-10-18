"""Operational / tail risks in the live heats since 10-17 22:00, rsi (trend_hold) vs default:
limit use (gross 250k / net 100k), position at the bell and last-vs-mid there, latency and 429s, worst block, and
how often two or more held stocks sit on the same side (one market-wide move hits both)."""
import glob, json, os, statistics as st
G = {"rsi": [], "default": []}
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    heat = os.path.basename(f)[10:25]
    if heat < "20261017-220000": continue
    R = [json.loads(l) for l in open(f)]
    s = next((r for r in R if r["k"] == "summary"), None)
    if not s: continue
    T = [r for r in R if r["k"] == "tick"]
    if not T: continue
    gross = max(sum(abs(v) for v in r["pos"].values()) for r in T)
    net = max(abs(sum(r["pos"].values())) for r in T)
    same = sum(1 for r in T if max(sum(v > 0 for v in r["pos"].values()), sum(v < 0 for v in r["pos"].values())) >= 2)
    last = T[-1]
    bell = sum(abs(v) for v in last["pos"].values())
    late = next((r for r in T if r["tick"] >= 400), last)
    late_pos = sum(abs(v) for v in late["pos"].values())
    lvm = sum(abs(v) * abs(last["last"][t] - (last["bid"][t] + last["ask"][t]) / 2) for t, v in last["pos"].items() if v)
    blocks = s.get("blocks") or []
    G["rsi" if "trend_hold" in os.path.basename(f) else "default"].append(dict(
        heat=heat, nlv=s["nlv"], gross=gross, net=net, same=same, bell=bell, late=late_pos, lvm=lvm,
        p95=s["latency_ms"]["p95"], mx=s["latency_ms"]["max"], q429=s["latency_ms"].get("throttled_429", 0),
        worst=min([b["made"] for b in blocks] or [0])))
for k, rows in G.items():
    print(f"=== {k}: {len(rows)} heats")
    print(f"  max gross used: mean {st.mean(r['gross'] for r in rows):,.0f}, max {max(r['gross'] for r in rows):,.0f} (limit 250k); "
          f"max net: mean {st.mean(r['net'] for r in rows):,.0f}, max {max(r['net'] for r in rows):,.0f} (limit 100k)")
    print(f"  ticks with 2+ stocks held on the same side: mean {st.mean(r['same'] for r in rows):.0f}/420")
    print(f"  shares held at tick 400: mean {st.mean(r['late'] for r in rows):,.0f}; at the bell: mean {st.mean(r['bell'] for r in rows):,.0f}, "
          f"heats with a position at the bell {sum(r['bell'] > 0 for r in rows)}; |last - mid| cost at the bell total ${sum(r['lvm'] for r in rows):,.0f}")
    print(f"  latency p95 mean {st.mean(r['p95'] for r in rows):.0f} ms, worst max {max(r['mx'] for r in rows):.0f} ms, 429s {sum(r['q429'] for r in rows)}")
    w = sorted(rows, key=lambda r: r["worst"])[:3]
    print(f"  worst blocks: " + ", ".join(f"{r['heat']} {r['worst']:+,.0f}" for r in w))
