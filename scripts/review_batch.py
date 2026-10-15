"""
Liability bot: the FROZEN version (b19e95c logic = config of commit b04253e, + S_staging promoted 2026-10-15) and its
three guards, heat by heat.

    python scripts/review_batch.py check [--set SECTION.KEY=VALUE ...]   # BEFORE a batch: is this the frozen bot?
    python scripts/review_batch.py review logs/data                      # AFTER a batch: journals (files, dirs, globs)

check  compares config/liability.toml (+ the --set overrides you will run with) with the frozen config; any trading
       difference is listed and the exit code is 1. Overrides that change nothing are ignored: a CROC edge_floor of 0,
       and the soft_* keys while soft_trend is off.
review prints each heat (final / lowest NLV, tenders, blocks, worst block, largest single-stock position, config
       check), the three guards that kept b19e95c positive in 14 evening heats (HANDOFF.md 2026-10-14 late night),
       and the two watch items. Pass every journal so far to pool the evidence.
"""
import glob
import json
import math
import os
import statistics as st
import subprocess
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the repo
sys.path.insert(0, os.path.join(ROOT, "src"))

FROZEN = "b04253e"          # commit whose config/liability.toml was the frozen bot until 2026-10-15
PROMOTED = {"strategy.drift_horizon_ticks": 20, "strategy.drift_zero_start": True,     # S_staging, made the default
            "strategy.liquidity_model": "flow_guarded"}                                # 2026-10-15 (user decision)
SECTIONS = ("case", "strategy", "execution", "risk")
OVERSTATE = 0.054           # the recorded-path estimate overstates real blocks by this much/share (17 evening heats)
FEE = 0.02


def flat(d: dict, p: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, f"{p}{k}."))
        else:
            out[f"{p}{k}"] = v
    return out


def effective(cfg: dict) -> dict:
    """Trading keys only, with per-ticker overrides that change nothing removed."""
    f = flat({k: v for k, v in cfg.items() if k in SECTIONS})
    for k in [k for k in f if k.startswith("strategy.per_ticker.")]:
        tk, key = k.split(".")[2], k.split(".", 3)[3]
        soft_on = f.get(f"strategy.per_ticker.{tk}.soft_trend", f.get("strategy.soft_trend", False))
        if (key == "edge_floor" and not f[k]) or (key.startswith("soft_") and not soft_on) \
                or f[k] == f.get(f"strategy.{key}"):
            f.pop(k)
    return f


def frozen() -> dict | None:
    try:
        text = subprocess.check_output(["git", "-C", ROOT, "show", f"{FROZEN}:config/liability.toml"], text=True,
                                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    return effective(tomllib.loads(text)) | PROMOTED


def diff(cfg: dict, base: dict) -> list[str]:
    return [f"{k} = {cfg.get(k)!r} (frozen: {base.get(k)!r})" for k in sorted(set(cfg) | set(base))
            if cfg.get(k) != base.get(k)]


def cmd_check(args: list[str]) -> int:
    from ritc.cli import set_path
    from ritc.core.config import load_case_config
    from ritc.tune import parse_value
    cfg = load_case_config("liability")
    sets = [a for a in args if a != "--set"]
    for s in sets:
        k, _, v = s.partition("=")
        set_path(cfg, k.strip(), parse_value(v.strip()))
    base = frozen()
    if base is None:
        print(f"cannot read commit {FROZEN} with git in {ROOT}: is git installed, and did you git pull?")
        return 2
    d = diff(effective(cfg), base)
    if d:
        print("NOT the frozen bot - these trading settings differ:\n  " + "\n  ".join(d))
        return 1
    print(f"OK: config/liability.toml{' + ' + ' '.join(sets) if sets else ''} = the frozen bot ({FROZEN} + S_staging)")
    return 0


def load(path: str) -> dict:
    rows = [json.loads(line) for line in open(path)]
    ticks = {r["tick"]: r for r in rows if r["k"] == "tick"}
    return dict(name=os.path.basename(path)[10:-6], rows=rows, ticks=ticks,
                seen={r["tid"]: r for r in rows if r["k"] == "tender_seen"},
                dec={r["tid"]: r for r in rows if r["k"] == "tender_decision"},
                blocks=[r for r in rows if r["k"] == "block_done"],
                summary=next((r for r in rows if r["k"] == "summary"), None),
                cfg=next((r.get("cfg") for r in rows if r["k"] == "start"), None))


def mid(h: dict, t: int, tk: str) -> float | None:
    r = h["ticks"].get(t)
    return (r["bid"][tk] + r["ask"][tk]) / 2 if r and r["bid"][tk] and r["ask"][tk] else None


def would_make(h: dict, s: dict) -> float | None:
    """$/share a declined fixed tender would have made: edge + the recorded mid over a 1,000-1,500 sh/tick unwind."""
    tk, sg = s["ticker"], 1 if s["action"] == "BUY" else -1
    vals = []
    for rate in (1000, 1500):
        ms = [mid(h, min(t, 419), tk) for t in range(s["tick"] + 1, s["tick"] + 1 + math.ceil(s["qty"] / rate))]
        ms = [m for m in ms if m is not None]
        if ms:
            vals.append(sg * (s["mid"] - s["price"]) + sg * (st.mean(ms) - s["mid"]) - FEE)
    return st.mean(vals) - OVERSTATE if vals else None


def cmd_review(args: list[str]) -> int:
    paths = []
    for a in args or ["logs/data"]:
        paths += sorted(glob.glob(os.path.join(a, "liability-*.jsonl"))) if os.path.isdir(a) else sorted(glob.glob(a))
    heats = [h for h in map(load, paths) if h["summary"] and h["ticks"]]
    if not heats:
        print("no finished heats found")
        return 1
    base = frozen()
    print(f"{'heat':16} {'final':>8} {'lowest':>8} {'fixed':>5} {'taken':>5} {'blocks +/-':>10} {'worst blk':>9} "
          f"{'max 1 stock':>11}  config")
    B, decl, flat_share = [], [], []
    for h in heats:
        nlv = [h["ticks"][t]["nlv"] for t in sorted(h["ticks"]) if t >= 5 and h["ticks"][t].get("nlv") is not None]
        fixed = [s for s in h["seen"].values() if s["fixed"]]
        taken = [s for s in fixed if h["dec"].get(s["tid"], {}).get("accept")]
        made = [b["made"] for b in h["blocks"]]
        one = max(max(abs(v) for v in r["pos"].values()) for r in h["ticks"].values())
        flat_share.append(sum(1 for r in h["ticks"].values() if not any(r["pos"].values())) / len(h["ticks"]))
        d = None if base is None or h["cfg"] is None else diff(effective(h["cfg"]), base)
        conf = "?" if d is None else ("frozen" if not d else "DIFFERS: " + "; ".join(d)[:90])
        print(f"{h['name']:16} {h['summary']['nlv']:+8,.0f} {min(nlv, default=0):+8,.0f} {len(fixed):5} {len(taken):5} "
              f"{sum(m > 0 for m in made):4}/{sum(m <= 0 for m in made):<5} {min(made, default=0):+9,.0f} "
              f"{one:11,.0f}  {conf}")
        for b in h["blocks"]:
            tid = b["tenders"][0]
            B.append(dict(b, value=h["dec"][tid]["pps"], tid=tid, n=len(b["tenders"])))
        for s in fixed:
            if not h["dec"].get(s["tid"], {}).get("accept") and s["price"] is not None:
                v = would_make(h, s)
                if v is not None:
                    edge = (1 if s["action"] == "BUY" else -1) * (s["mid"] - s["price"])
                    decl.append((s["tid"], edge, v, s["qty"]))
    final = [h["summary"]["nlv"] for h in heats]
    print(f"\n{len(heats)} heats: final mean {st.mean(final):+,.0f}, worst {min(final):+,.0f}, "
          f"negative {sum(x < 0 for x in final)}")

    print("\nGUARD 1  ENTRY CUSHION (declines; frozen bot, 14 evening heats: obvious -0.377/sh, close calls ~0)")
    for lab, sel in (("edge <= 0.10 (obvious)", lambda e: e <= 0.10), ("edge > 0.10 (close call)", lambda e: e > 0.10)):
        s = [(v, q) for _, e, v, q in decl if sel(e)]
        if s:
            print(f"  declined, {lab:24} n {len(s):3}  would have made {st.mean(v for v, _ in s):+.3f}/sh, "
                  f"total {sum(v * q for v, q in s) / 1e3:+,.0f}k")
    if B:
        print(f"  taken blocks {len(B)}: mean {st.mean(b['made'] for b in B):+,.0f}, "
              f"{sum(b['made'] > 0 for b in B)} positive")

    print("\nGUARD 2  FAST EXIT (blocks the price ran > 0.20 against; frozen: 14 s, 92% crossed, worst -16.4k)")
    bad = [b for b in B if b["mid_move"] < -0.20]
    if bad:
        crossed = st.mean(b["crossed"] / b["qty"] for b in bad)
        print(f"  n {len(bad)}: {st.mean(b['secs'] for b in bad):.0f} s, crossed {crossed:.0%},"
              f" made mean {st.mean(b['made'] for b in bad):+,.0f}, worst {min(b['made'] for b in bad):+,.0f}")
    else:
        print("  none")

    print("\nGUARD 3  SMALL EXPOSURE (frozen: one block per stock <= 55k, flat 84% of the heat, auctions 0 won)")
    won = sum(1 for h in heats for tid, t in (h["summary"].get("tenders") or {}).items()
              if t.get("won") and not h["seen"].get(int(tid), {}).get("fixed", True))
    stacked = sum(1 for b in B if b["n"] > 1)
    print(f"  flat {st.mean(flat_share):.0%} of the time, auctions won {won}, blocks stacked from 2+ tenders {stacked}")

    print("\nWATCH (act only after ~10 more heats agree)")
    watch = (("CROC blocks valued < 0.10 (frozen: 3 of 3 lost, -12.5k)",
              lambda b: b["ticker"] == "CROC" and 0 <= b["value"] < 0.10),
             ("slot #102, ~tick 85 (frozen: 4 of 5 lost)", lambda b: b["tid"] == 102))
    for lab, sel in watch:
        s = [b["made"] for b in B if sel(b) and b["n"] == 1]
        line = f"n {len(s):2}, lost {sum(m < 0 for m in s)}, total {sum(s) / 1e3:+.1f}k" if s else "none"
        print(f"  {lab:58} {line}")
    return 0


if __name__ == "__main__":
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("review", [])
    sys.exit({"check": cmd_check, "review": cmd_review}[cmd](rest))
