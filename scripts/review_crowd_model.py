"""
CROWD TENDER MODEL review (profile crowd_model): every tender the model ACCEPTED over the bot's price decline, and the
realized P&L of the block it ended up in (block_done "made", split by expected $ when a block carried several tenders).

    python scripts/review_crowd_model.py [logs/data]

Decision rule for the competition (fixed 2026-10-18 before the batch): switch it on in config/liability.toml if the
model's accepts made >= $0 in total, none lost more than $25k, and the batch had 0 fines. No crowded heat = no
new evidence: decide on the out-of-time test (+$10.8k/heat, t 2.2, docs/results/rsi_hold_2026-10-17).
"""
import glob
import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "logs/data"
total, n, worst, heats, fines = 0.0, 0, 0.0, 0, 0.0
for f in sorted(glob.glob(os.path.join(root, "liability-*__crowd_model*.jsonl"))):
    recs = [json.loads(line) for line in open(f)]
    end = next((r for r in recs if r["k"] == "summary"), None)
    if not end:
        continue
    heats += 1
    market = [r for r in recs if r["k"] == "market" and isinstance(r.get("trader"), dict)]
    fines = max(fines, float((market[-1]["trader"].get("total_fines") or 0.0)) if market else 0.0)
    model = {r["tid"]: r for r in recs if r["k"] == "tender_decision" and str(r.get("reason", "")).startswith("tender model")}
    for b in (r for r in recs if r["k"] == "block_done"):
        tids = [t for t in b.get("tenders", []) if t in model]
        if tids:
            n += len(tids)
            total += b["made"]
            worst = min(worst, b["made"])
            print(f"  {os.path.basename(f)[10:25]} {b['ticker']} {b['side']} {b['qty']:,} block made {b['made']:+,.0f} "
                  f"(expected {b['expected']:+,.0f}; tenders {b.get('tenders')}, model accepts {tids})")
    for tid, r in model.items():
        if not any(tid in b.get("tenders", []) for b in recs if b["k"] == "block_done"):
            print(f"  {os.path.basename(f)[10:25]} tender {tid}: accepted by the model, block not finished (held to the bell?)")
print(f"{heats} crowd_model heats, {n} model accepts, their blocks made {total:+,.0f} (worst {worst:+,.0f}), max fines {fines:,.0f}")
print("VERDICT:", "no model accepts yet (no crowded heat?)" if not n else
      ("ON for the competition" if total >= 0 and worst > -25_000 and fines == 0 else "keep it OFF"))
