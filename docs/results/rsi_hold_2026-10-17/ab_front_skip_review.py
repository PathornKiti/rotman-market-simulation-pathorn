"""Default (A_default) vs FRONT SKIP (K_front_skip), live batch 2026-10-17: per session, crowded or not, per block."""
import glob, json, re, statistics as st, math, os
ROOT = "logs"
def t_(x): return st.mean(x) / (st.stdev(x) / math.sqrt(len(x))) if len(x) > 1 and st.stdev(x) else float("nan")
heats = []
for f in sorted(glob.glob(f"{ROOT}/data/liability-20261017-*.jsonl")):
    name = os.path.basename(f)[len("liability-"):-len(".jsonl")]
    stamp, prof = name[:15], name.split("__")[1]
    recs = [json.loads(l) for l in open(f)]
    summ = next((r for r in recs if r["k"] == "summary"), None)
    if not summ:
        continue
    log = open(f"{ROOT}/{os.path.basename(f)[:-6]}.log", errors="replace").read()
    crowded = "-> CROWDED" in log
    blocks = [r for r in recs if r["k"] == "block_done"]
    skips = re.findall(r"FRONT (\w+) skipped", log)
    taken = [r for r in recs if r["k"] == "tender_decision" and r.get("accept")]
    hh = int(stamp[9:11])
    sess = "00-01 night" if hh < 6 else ("09-12 morning" if hh < 18 else "22-23 real (6 traders)")
    heats.append(dict(stamp=stamp, prof=prof, nlv=summ["nlv"], crowded=crowded, blocks=blocks, skips=skips,
                      sess=sess, exp=sum(b["expected"] for b in blocks), made=sum(b["made"] for b in blocks)))
A = "A_default"; K = "K_front_skip"
def line(lab, hs):
    if not hs: return f"  {lab:28} n 0"
    n = [h["nlv"] for h in hs]
    return (f"  {lab:28} n {len(hs):2}  NLV mean {st.mean(n):>8,.0f}  median {st.median(n):>8,.0f}  worst {min(n):>8,.0f}"
            f"  blocks made/expected {sum(h['made'] for h in hs)/max(1,sum(h['exp'] for h in hs)):.0%}")
for key, lab in (("sess", "session"), ("crowded", "crowd alarm")):
    print(f"== by {lab}")
    for v in sorted({h[key] for h in heats}, key=str):
        for p in (A, K):
            print(line(f"{v} {p}", [h for h in heats if h[key] == v and h["prof"] == p]))
print("\n== blocks: made vs expected, by profile and whether the front slice was skipped")
for p in (A, K):
    rows = []
    for h in heats:
        if h["prof"] != p: continue
        sk = list(h["skips"])
        for b in h["blocks"]:
            skipped = b["ticker"] in sk
            if skipped: sk.remove(b["ticker"])
            rows.append((skipped, b["made"] - b["expected"], b["made"], b["mid_move"] if b.get("mid_move") is not None else 0))
    for s in (False, True):
        r = [x for x in rows if x[0] == s]
        if r:
            print(f"  {p:13} {'skipped ' if s else 'front cut'}: n {len(r):3}  made-expected mean {st.mean(x[1] for x in r):>+8,.0f} "
                  f"(t {t_([x[1] for x in r]):+.1f})  losing {sum(x[2] < 0 for x in r)}  mid move {st.mean(x[3] for x in r):+.3f}")
