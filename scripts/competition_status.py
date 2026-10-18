"""Competition scoreboard + switch rule, run between heats (Windows: scripts\\competition_status.bat).

Per finished heat since --since (default: today 12:00): profile, NLV, crowd alarm (any time in the heat), fixed tenders
taken, touch accepts, holds, what the held blocks made, the worst block. Then the switch rule fixed in advance
(docs/COMPETITION_RUNBOOK.md):

    SWITCH to the default bot (scripts\\run_competition_safe.bat) only if the LAST TWO rsi_bot heats both had the crowd
    alarm on AND both lost money on held blocks. Anything else: stay on rsi_bot (one bad heat is normal noise).

Usage: python scripts/competition_status.py [logs] [--since 20261018-120000]
"""
import datetime as dt
import glob
import json
import os
import statistics as st
import sys


def heat_row(path: str) -> dict | None:
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    s = next((r for r in rows if r["k"] == "summary"), None)
    if not s:
        return None
    base = os.path.basename(path)[: -len(".jsonl")]
    log = os.path.join(os.path.dirname(os.path.dirname(path)), base + ".log")
    text = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
    crowd = bool((s.get("crowd") or {}).get("crowded")) or " CROWDED" in text
    dec = [r for r in rows if r["k"] == "tender_decision"]
    touch = sum(1 for d in dec if d["accept"] and str(d.get("reason", "")).startswith("touch edge"))
    offs = [(r["ticker"], r["tick"]) for r in rows if r["k"] == "trend_hold" and r["ticks"] > 0]
    blocks = s.get("blocks") or []
    held = [b for b in blocks if any(t == b["ticker"] and abs(k - b["start_tick"]) <= 3 for t, k in offs)]
    fines = max([(r.get("trader") or {}).get("total_fines") or 0 for r in rows if r["k"] == "market"] or [0])
    return dict(heat=base[10:25], prof=s.get("profile") or "?", nlv=s["nlv"], crowd=crowd,
                taken=sum(1 for t in (s.get("tenders") or {}).values() if t.get("kind") == "fixed" and t.get("won")),
                touch=touch, holds=len(offs), held_made=sum(b["made"] for b in held),
                worst=min([b["made"] for b in blocks] or [0]), fines=fines)


def main(argv: list[str]) -> int:
    since = dt.date.today().strftime("%Y%m%d") + "-120000"
    if "--since" in argv:
        i = argv.index("--since")
        since, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    root = argv[0] if argv else "logs"
    rows = [r for r in (heat_row(f) for f in sorted(glob.glob(os.path.join(root, "data", "liability-*.jsonl")))
                        if os.path.basename(f)[10:25] >= since) if r]
    if not rows:
        print(f"no finished heats since {since} in {root}\\data")
        return 0
    print(f"{len(rows)} finished heats since {since}\n")
    print(f"{'heat':16}{'profile':11}{'NLV':>10}  {'crowd':6}{'taken':>5}{'touch':>6}{'holds':>6}{'held $':>10}"
          f"{'worst blk':>11}{'fines':>7}")
    for r in rows:
        print(f"{r['heat']:16}{r['prof'][:10]:11}{r['nlv']:>10,.0f}  {'ALARM' if r['crowd'] else '':6}{r['taken']:>5}"
              f"{r['touch']:>6}{r['holds']:>6}{r['held_made']:>+10,.0f}{r['worst']:>+11,.0f}{r['fines']:>7,.0f}")
    x = [r["nlv"] for r in rows]
    print(f"\nall: mean {st.mean(x):,.0f}  median {st.median(x):,.0f}  worst {min(x):,.0f}  "
          f">= $70k {sum(v >= 70_000 for v in x)}/{len(x)}  fines {sum(r['fines'] for r in rows):,.0f}")
    rsi = [r for r in rows if r["prof"] == "rsi_bot"]
    last2 = rsi[-2:]
    bad = [r for r in last2 if r["crowd"] and r["held_made"] < 0]
    print("\nSWITCH RULE (last two rsi_bot heats: crowd alarm AND held blocks lost):")
    for r in last2:
        print(f"  {r['heat']}: alarm {'yes' if r['crowd'] else 'no'}, held blocks {r['held_made']:+,.0f}"
              f" -> {'BAD' if r in bad else 'ok'}")
    if len(last2) == 2 and len(bad) == 2:
        print("  => SWITCH: close this bot (Ctrl-C) and start scripts\\run_competition_safe.bat before the next heat")
    else:
        print("  => STAY on rsi_bot")
    if any(r["fines"] for r in rows):
        print("  !! RIT shows fines: read that heat's log (positions through zero?) before the next heat")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
