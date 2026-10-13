"""
Practice-heat scoreboard (liability): one line per real heat from the bot's run journals, so profiles can be
compared on the real server (docs/RUNBOOK.md, "Practice-heat A/B testing").

    python -m ritc practice logs/liability-*.jsonl

Per heat: the profile it ran (liquidity model, crowd_robust, end-game and trend switches), final NLV, fixed
tenders taken and their estimated edge, auction bids and WINS, crowd alarms and loop speed. Then every auction
bid with its distance from the mid, won or lost: across heats that brackets where the real reserve sits.

An auction counts as won when the position in its stock jumps by >= 80% of its size in the bid's direction within
a few ticks of the window closing (RIT books the award at expiry; the journal has no award record).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def load_profiles(case: str = "liability") -> dict[str, dict]:
    """config/<case>_profiles.toml -> {name: {"section.key": value}} in file order."""
    import tomllib

    from .core.config import CONFIG_DIR
    p = CONFIG_DIR / f"{case}_profiles.toml"
    if not p.exists():
        raise FileNotFoundError(f"no profiles at {p}")
    with p.open("rb") as fh:
        return {k: dict(v) for k, v in tomllib.load(fh).items()}


def pick_profile(case: str, name: str, logs: str = "logs") -> tuple[str, dict]:
    """A named profile, or for 'rotate' the one with the fewest LIVE heats in `logs` (ties: file order)."""
    profiles = load_profiles(case)
    if name != "rotate":
        if name not in profiles:
            raise SystemExit(f"unknown profile {name!r}; have: {', '.join(profiles)}")
        return name, profiles[name]
    used = {k: 0 for k in profiles}
    for j in Path(logs).glob(f"{case}-*.jsonl"):
        recs = _load(j)
        if not any(r.get("k") == "tick" for r in recs):
            continue                          # started but never traded (Ctrl-C before the heat): not a heat
        for r in recs[:3]:
            if r.get("k") == "start":
                prof = ((r.get("cfg") or {}).get("strategy") or {}).get("profile")
                if prof in used and not r.get("dry_run"):
                    used[prof] += 1
                break
    best = min(used, key=lambda k: (used[k], list(profiles).index(k)))
    return best, profiles[best]


def variance_ratio(mids: list[float], w: int = 10) -> float | None:
    """Var(w-tick change) / (w x Var(1-tick change)): 1 = random walk, > 1 = trends persist, < 1 = mean reversion."""
    d1 = [b - a for a, b in zip(mids, mids[1:])]
    dw = [b - a for a, b in zip(mids, mids[w:])]
    if len(d1) < 5 * w or len(dw) < 5:
        return None
    v1 = statistics.pvariance(d1)
    return statistics.pvariance(dw) / (w * v1) if v1 > 0 else None


PROFILE_KEYS = (("strategy", "liquidity_model", "liq"), ("strategy", "crowd_robust", "crowd_robust"),
                ("strategy", "decide_late_ticks", "late"), ("execution", "hold_budget_min_ticks", "flat_bell"),
                ("execution", "close_hold_ticks", "hold_end"), ("execution", "trend_unwind_z", "trend_z"))


def _load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summarize(path: str | Path) -> dict:
    path = Path(path)
    recs = _load(path)
    start = next((r for r in recs if r.get("k") == "start"), {})
    cfg = start.get("cfg") or {}
    profile = {}
    for sec, key, short in PROFILE_KEYS:
        v = (cfg.get(sec) or {}).get(key)
        if v not in (None, False, 0, 0.0) or short in ("liq", "hold_end"):
            profile[short] = v
    ticks = [r for r in recs if r.get("k") == "tick"]
    vr = {}
    for t in sorted({t for r in ticks for t in (r.get("bid") or {})}):
        mids = [(r["bid"][t] + r["ask"][t]) / 2 for r in ticks
                if (r.get("bid") or {}).get(t) and (r.get("ask") or {}).get(t)]
        v = variance_ratio(mids)
        if v is not None:
            vr[t] = round(v, 2)
    end = next((r for r in reversed(recs) if r.get("k") == "end"), None)
    nlv = end.get("nlv") if end and end.get("nlv") is not None else (ticks[-1].get("nlv") if ticks else None)
    seen = {r["tid"]: r for r in recs if r.get("k") == "tender_seen"}
    dec = {r["tid"]: r for r in recs if r.get("k") == "tender_decision" and not r.get("dry_run")}
    ans = {r["tid"]: r for r in recs if r.get("k") == "tender_answer"}
    pos_at = {r["tick"]: r.get("pos") or {} for r in ticks}

    def pos(tick: int, ticker: str) -> int | None:
        for k in range(tick, tick - 4, -1):
            if k in pos_at:
                return int(pos_at[k].get(ticker, 0))
        return None

    fixed_taken, fixed_edge, fixed_offered, auctions = 0, 0.0, 0, []
    for tid, s in seen.items():
        d, a = dec.get(tid), ans.get(tid)
        if s.get("fixed", True):
            fixed_offered += 1
            if d and d.get("accept") and a and a.get("answer") == "accept":
                fixed_taken += 1
                fixed_edge += float(d.get("pps") or 0) * int(s.get("qty") or 0)
            continue
        if not (d and d.get("accept") and d.get("price") is not None):
            auctions.append({"tid": tid, "ticker": s.get("ticker"), "action": s.get("action"), "qty": s.get("qty"),
                             "bid": None, "vs_mid": None, "won": False, "why": (d or {}).get("reason", "no bid")})
            continue
        sign = 1 if str(s.get("action")).upper() == "BUY" else -1
        mid = d.get("mid") or s.get("mid")
        exp = int(s.get("expires") or d.get("tick") or 0)
        before, won = pos(exp - 1, s["ticker"]), False
        if before is not None:
            for k in range(exp, exp + 5):
                after = pos(k, s["ticker"])
                if after is not None and sign * (after - before) >= 0.8 * int(s.get("qty") or 0):
                    won = True
                    break
        auctions.append({"tid": tid, "ticker": s.get("ticker"), "action": s.get("action"), "qty": s.get("qty"),
                         "bid": d["price"], "won": won, "why": "",
                         # + = how much better than the mid our bid was for the CLIENT (closer to winning)
                         "vs_mid": round(sign * (d["price"] - mid), 3) if mid else None})
    loop = [float(r.get("loop_ms") or 0) for r in ticks if r.get("loop_ms")]
    crowded = 0
    log = path.with_suffix(".log")
    if log.exists():
        crowded = sum("CROWDED" in line for line in log.read_text(encoding="utf-8", errors="replace").splitlines())
    bids = [a for a in auctions if a["bid"] is not None]
    name = (cfg.get("strategy") or {}).get("profile") or "-"
    heat = path.stem.split("-", 1)[-1]           # date-time__what the run changed
    return {"file": path.name, "heat": heat, "name": name, "vr": vr, "profile": profile, "nlv": nlv,
            "fixed_offered": fixed_offered,
            "fixed_taken": fixed_taken, "fixed_edge": fixed_edge, "auctions": auctions, "bids": len(bids),
            "wins": sum(a["won"] for a in bids), "crowded": crowded,
            "loop_p50": statistics.median(loop) if loop else None,
            "slow": sum(r.get("k") == "slow_loop" for r in recs)}


def _profile_str(p: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in p.items())


def report(paths: list[str]) -> str:
    rows = [r for r in (summarize(p) for p in paths) if r["nlv"] is not None]   # skip runs that never traded
    if not rows:
        return "no heats with ticks in these journals"
    w = max([len(r["heat"]) for r in rows] + [4]) + 2
    out = [f"{'heat':<{w}}{'profile':<16}{'NLV':>10}{'fixed':>8}{'edge':>9}{'auct':>7}{'crowd':>7}{'loop':>7}"
           f"  trend VR(10)"]
    for r in rows:
        out.append(f"{r['heat']:<{w}}{r['name'][:15]:<16}{(r['nlv'] or 0):>10,.0f}"
                   f"{r['fixed_taken']:>4}/{r['fixed_offered']:<3}{r['fixed_edge']:>9,.0f}{r['wins']:>4}/{r['bids']:<2}"
                   f"{r['crowded']:>7}{(r['loop_p50'] or 0):>6.0f}ms  "
                   + " ".join(f"{t} {v:.2f}" for t, v in r["vr"].items()))
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(f"{r['name']}: {_profile_str(r['profile'])}", []).append(r)
    out.append("\nby profile (mean NLV, auction wins / bids, fixed tenders taken per heat):")
    for prof, rs in groups.items():
        nl = [r["nlv"] for r in rs if r["nlv"] is not None]
        out.append(f"  {len(rs)} heat(s)  NLV {statistics.mean(nl) if nl else 0:>9,.0f}"
                   f"{'' if len(nl) < 2 else f' (sd {statistics.stdev(nl):,.0f})'}"
                   f"  auctions {sum(r['wins'] for r in rs)}/{sum(r['bids'] for r in rs)}"
                   f"  fixed {sum(r['fixed_taken'] for r in rs) / len(rs):.1f}/heat  |  {prof}")
    allvr = [v for r in rows for v in r["vr"].values()]
    if allvr:
        hi = sum(v > 1.2 for v in allvr)
        out.append(f"\ntrend test: variance ratio over 10 ticks, {len(allvr)} stock-heats: median "
                   f"{statistics.median(allvr):.2f}, {hi} above 1.2 (random walk ~1.0, sd ~0.2 per stock-heat). "
                   + ("Trends look real: try profile D_trend." if statistics.median(allvr) > 1.2 and hi > len(allvr) / 2
                      else "No clear trend: keep trend_unwind_z off."))
    bids = [(r["heat"], a) for r in rows for a in r["auctions"] if a["bid"] is not None]
    if bids:
        out.append("\nauction bids (vs_mid = how far our bid sat from the mid in the client's favour; the real reserve "
                   "is above the lost ones and at or below the won ones):")
        for f, a in sorted(bids, key=lambda x: -(x[1]["vs_mid"] or -9)):
            out.append(f"  {'WON ' if a['won'] else 'lost'} #{a['tid']:<4} {a['action']:<4} {a['ticker']:<5} "
                       f"{int(a['qty'] or 0) // 1000:>3}k  bid {a['bid']:<8} vs_mid {a['vs_mid']:+.2f}   {f}")
        won = [a["vs_mid"] for _, a in bids if a["won"] and a["vs_mid"] is not None]
        lost = [a["vs_mid"] for _, a in bids if not a["won"] and a["vs_mid"] is not None]
        if won or lost:
            lo = f"lost up to {max(lost):+.2f}" if lost else "no losses"
            hi = (f"won from {min(won):+.2f}" if won
                  else f"no wins yet: the reserve is above {max(lost):+.2f} from the mid (bid closer: profile C)")
            out.append(f"  reserve bracket: {lo}, {hi} (vs mid, client's favour)")
    blocks = []
    for p in paths:
        sj = Path(p).with_suffix("").with_suffix(".summary.json") if p.endswith(".jsonl") else None
        if sj and sj.exists():
            try:
                doc = json.loads(sj.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            blocks += [dict(b, heat=Path(p).stem.split("-", 1)[-1][:15]) for b in doc.get("blocks", [])]
    if blocks:
        out.append("\nunwound blocks, slowest first (rate = shares/s absorbed; mid = move over the unwind, "
                   "+ = our way):")
        for b in sorted(blocks, key=lambda b: b["rate"]):
            out.append(f"  {b['heat']}  {b['ticker']:<5}{b['side']:<5}{b['qty'] // 1000:>3}k {b['secs']:>4}s "
                       f"{b['rate']:>6,}/s  mid {b['mid_move']:+.2f}  rested {b['rested']:>7,} crossed "
                       f"{b['crossed']:>7,}  made {b['made']:>+8,} vs exp {b['expected']:>+8,}")
        if len(blocks) >= 4:
            r = [b["rate"] for b in blocks]
            m = [b["mid_move"] for b in blocks]
            mr, mm = statistics.mean(r), statistics.mean(m)
            cov = sum((a - mr) * (c - mm) for a, c in zip(r, m))
            den = (sum((a - mr) ** 2 for a in r) * sum((c - mm) ** 2 for c in m)) ** 0.5
            if den:
                out.append(f"  correlation(absorption rate, move our way) = {cov / den:+.2f} over {len(blocks)} blocks "
                           "(clearly positive = slow unwinds lose: the stuck-inventory risk is real)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    paths = [p for p in paths if p.endswith(".jsonl")]
    if not paths:
        print("usage: python -m ritc practice logs/liability-*.jsonl")
        return 1
    print(report(paths))
    return 0
