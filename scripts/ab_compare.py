"""
Compare the profiles of a live A/B batch (liability), heat by heat and block by block.

    python scripts/ab_compare.py [journals, dirs or globs ...]     # default: logs/data
    python scripts/ab_compare.py logs/data --since 20261017-000000  # only heats from this time on

The profile is read from the file name (`liability-<time>__<profile>__...`). Per profile: heats, NLV (mean / median /
worst), tenders taken, the bot's expected value of what it took (the tender draw), edge kept, mid move against during
unwinds, losing blocks, crowd-alarm heats. Then the difference of the second profile vs the first: Welch t, heats run
back to back (non-overlapping pairs), losing blocks (Fisher), and how much of the NLV gap is the tender draw.
FRONT lines from the .log next to each journal (logs/<name>.log): slices crossed, slices SKIPPED and the mid over the
20 ticks after each skip (+ = moved our way, so the skip was right).
"""
import bisect
import glob
import json
import math
import os
import re
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TD = re.compile(r"^(\d\d:\d\d:\d\d\.\d+) .*TENDER (\d+) ")
SKIP = re.compile(r"^(\d\d:\d\d:\d\d\.\d+) .*FRONT (\w+) skipped")
FDONE = re.compile(r"^(\d\d:\d\d:\d\d\.\d+) .*FRONT (\w+) done: ([+-]\d+) of the ([+-]\d+) block")


def profile_of(path: str) -> str:
    parts = os.path.basename(path)[:-len(".jsonl")].split("__")
    return parts[1] if len(parts) > 1 else "default"


def tstat(x: list[float]) -> float:
    return st.mean(x) / (st.stdev(x) / len(x) ** 0.5) if len(x) > 1 and st.stdev(x) else 0.0


def fisher_greater(a: int, n1: int, b: int, n2: int) -> float:
    """One-sided Fisher exact p that the first group has at least `a` of the a + b events."""
    k, n = a + b, n1 + n2
    return sum(math.comb(n1, i) * math.comb(n2, k - i) for i in range(a, min(k, n1) + 1)) / math.comb(n, k)


def front_lines(log: str, rows: list[dict]) -> tuple[list[float], list[float]]:
    """(share crossed of each FRONT slice, mid move our way over the 20 ticks after each FRONT skip)."""
    if not os.path.exists(log):
        return [], []
    ticks = {r["tick"]: r for r in rows if r["k"] == "tick"}
    stamps = sorted((r["w"], r["tick"]) for r in rows if r["k"] == "tick")
    dec = {r["tid"]: r["w"] for r in rows if r["k"] == "tender_decision"}
    pos_side = {}                                     # ticker -> sign of the block being unwound (from the tick rows)
    lines = open(log, errors="replace").read().splitlines()
    def sec(s: str) -> float:
        return int(s[:2]) * 3600 + int(s[3:5]) * 60 + float(s[6:])
    offs = [dec[int(m.group(2))] - sec(m.group(1))
            for line in lines if (m := TD.match(line)) and int(m.group(2)) in dec]
    if not offs or not stamps:
        return [], []
    off = st.median(offs)

    def tick_at(s: str) -> int:
        i = bisect.bisect_right(stamps, (sec(s) + off, 1e18)) - 1
        return stamps[max(0, i)][1]

    def mid(t: int, k: str) -> float | None:
        r = ticks.get(min(t, 419))
        return (r["bid"][k] + r["ask"][k]) / 2 if r and r["bid"].get(k) and r["ask"].get(k) else None

    sizes, skips = [], []
    for line in lines:
        if (m := FDONE.match(line)):
            sizes.append(int(m.group(3)) / int(m.group(4)))
        elif (m := SKIP.match(line)):
            t0, k = tick_at(m.group(1)), m.group(2)
            r = ticks.get(t0 + 1) or ticks.get(t0)
            side = (r or {}).get("pos", {}).get(k, 0) if r else 0
            pos_side[k] = 1 if side > 0 else -1 if side < 0 else 0
            a, b = mid(t0, k), mid(t0 + 20, k)
            if a and b and pos_side[k]:
                skips.append(pos_side[k] * (b - a))
    return sizes, skips


def load(path: str) -> dict | None:
    rows = [json.loads(line) for line in open(path)]
    s = next((r for r in rows if r["k"] == "summary"), None)
    if not s:
        return None
    seen = {r["tid"]: r for r in rows if r["k"] == "tender_seen"}
    tend = s.get("tenders") or {}
    blocks = []
    for b in (r for r in rows if r["k"] == "block_done"):
        ts = [seen[i] for i in b["tenders"] if i in seen and seen[i].get("price")]
        edge = sum((1 if x["action"] == "BUY" else -1) * (x["mid"] - x["price"]) * x["qty"] for x in ts)
        exp = sum((tend.get(str(i)) or {}).get("expected") or 0 for i in b["tenders"])
        blocks.append(dict(made=b["made"], exp=exp, edge=edge, qty=b["qty"], mm=b["mid_move"]))
    name = os.path.basename(path)[:-len(".jsonl")]
    sizes, skips = front_lines(os.path.join(os.path.dirname(os.path.dirname(path)), name + ".log"), rows)
    return dict(heat=name[10:25], prof=profile_of(path), nlv=s["nlv"], blocks=blocks, sizes=sizes, skips=skips,
                crowd=bool((s.get("crowd") or {}).get("crowded")),
                taken=sum(1 for t in tend.values() if t.get("kind") == "fixed" and t.get("won")))


def main(argv: list[str]) -> int:
    since = None
    if "--since" in argv:
        i = argv.index("--since")
        since, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    paths = []
    for a in argv or [os.path.join(ROOT, "logs", "data")]:
        paths += sorted(glob.glob(os.path.join(a, "liability-*.jsonl"))) if os.path.isdir(a) else sorted(glob.glob(a))
    H = [h for h in map(load, sorted(set(paths), key=os.path.basename)) if h and (not since or h["heat"] >= since)]
    if not H:
        print("no finished heats found")
        return 1
    profs = list(dict.fromkeys(h["prof"] for h in sorted(H, key=lambda h: h["heat"])))
    profs = sorted(profs, key=lambda p: (p != "A_default", profs.index(p)))   # the default first when present
    print(f"{len(H)} finished heats, {H[0]['heat']} .. {H[-1]['heat']}\n")
    print(f"{'profile':16} {'heats':>5} {'NLV mean':>9} {'median':>8} {'worst':>8} {'taken':>5} {'expected':>9} "
          f"{'edge kept':>9} {'mid/sh':>7} {'blocks':>6} {'losing':>6} {'crowd':>5}  FRONT")
    for p in profs:
        hs = [h for h in H if h["prof"] == p]
        b = [x for h in hs for x in h["blocks"]]
        n = [h["nlv"] for h in hs]
        q = sum(x["qty"] for x in b) or 1
        edge = sum(x["edge"] for x in b)
        sizes = [x for h in hs for x in h["sizes"]]
        skips = [x for h in hs for x in h["skips"]]
        front = (f"{len(sizes)} slices ({st.mean(sizes):.0%})" if sizes else "no slices") + \
                (f", {len(skips)} skipped (mid +20 ticks {st.mean(skips):+.3f} our way, t {tstat(skips):+.1f})"
                 if skips else "")
        print(f"{p:16} {len(hs):5} {st.mean(n):+9,.0f} {st.median(n):+8,.0f} {min(n):+8,.0f} "
              f"{st.mean(h['taken'] for h in hs):5.1f} {sum(x['exp'] for x in b) / len(hs):+9,.0f} "
              f"{sum(x['made'] for x in b) / edge if edge else 0:9.0%} {sum(x['mm'] * x['qty'] for x in b) / q:+7.3f} "
              f"{len(b):6} {sum(x['made'] < 0 for x in b):6} {sum(h['crowd'] for h in hs):5}  {front}")
    if len(profs) < 2:
        return 0
    a, c = profs[0], profs[1]
    A = [h for h in H if h["prof"] == a]
    B = [h for h in H if h["prof"] == c]
    if len(A) < 2 or len(B) < 2:
        print(f"\n{c} vs {a}: need at least 2 heats of each")
        return 0
    na, nb = [h["nlv"] for h in A], [h["nlv"] for h in B]
    se = (st.variance(na) / len(na) + st.variance(nb) / len(nb)) ** 0.5
    def per_heat(hs: list[dict], f) -> float:
        return st.mean(sum(f(x) for x in h["blocks"]) for h in hs)
    draw = per_heat(B, lambda x: x["exp"]) - per_heat(A, lambda x: x["exp"])
    ex = per_heat(B, lambda x: x["made"] - x["exp"]) - per_heat(A, lambda x: x["made"] - x["exp"])
    gap = st.mean(nb) - st.mean(na)
    print(f"\n{c} vs {a}: NLV {gap:+,.0f}/heat (Welch t {gap / se:+.2f}); "
          f"of it the tender draw (expected value taken) {draw:+,.0f}, execution (made - expected) {ex:+,.0f}")
    seq = sorted(H, key=lambda h: h["heat"])
    pairs, i = [], 0
    while i + 1 < len(seq):                            # back-to-back heats of the two profiles, each heat used once
        x, y = seq[i], seq[i + 1]
        if {x["prof"], y["prof"]} == {a, c} and x["heat"][:8] == y["heat"][:8]:
            pairs.append((y if y["prof"] == c else x)["nlv"] - (x if x["prof"] == a else y)["nlv"])
            i += 2
        else:
            i += 1
    if len(pairs) > 1:
        print(f"  back-to-back pairs {len(pairs)}: {st.mean(pairs):+,.0f}/heat (t {tstat(pairs):+.2f}), "
              f"{c} better {sum(d > 0 for d in pairs)}/{len(pairs)}")
    la, lb = sum(x["made"] < 0 for h in A for x in h["blocks"]), sum(x["made"] < 0 for h in B for x in h["blocks"])
    ba, bb = sum(len(h["blocks"]) for h in A), sum(len(h["blocks"]) for h in B)
    if ba and bb:
        p = fisher_greater(*((la, ba, lb, bb) if la / ba >= lb / bb else (lb, bb, la, ba)))
        print(f"  losing blocks {a} {la}/{ba} vs {c} {lb}/{bb} (one-sided Fisher p {p:.3f})")
    crowded = [h for h in H if h["crowd"]]
    listed = ", ".join(f"{h['prof']} {h['nlv']:+,.0f}" for h in crowded) or "none"
    print(f"  crowd-alarm heats: {len(crowded)} ({listed})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
