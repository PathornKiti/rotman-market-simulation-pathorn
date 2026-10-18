"""Candidate fixes for the gaps (gaps.py), replayed on every recorded heat (backtest.py mids, gradual exits 2,500 sh/tick
at mid - 0.03, fee 0.02, limits gross 250k / net 100k). Base = rsi_bot: accept every fixed tender priced better than the
touch (plus the bot's own accepts), hold while the last L ticks moved our way, checked every C ticks.
  base      L 10, C 10                       (live rsi_bot)
  C5        L 10, checked every 5 ticks      (exit sooner -> less giveback?)
  L5C5      L 5,  checked every 5 ticks
  own       base but only the bot's own accepts (no touch accepts)
  rel0.10   base + ROOM RELEASE: a tender that beats the touch by >= 0.10 but doesn't fit the limits ends the holds
            (oldest first) until it fits, and is accepted
  rel0.20   same, >= 0.20
Holdout: split by date (before / from 10-17 22:00, the ~6-person sessions)."""
import glob, json, os, sys, statistics as st, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "rsi_hold_2026-10-17")]
import backtest as B
RATE, COST, FEE = 2500, 0.03, 0.02
def run(mid, last, tenders, own, L=10, C=10, touch=True, release=None, frac=1.0):
    pos = {t: 0 for t in B.TICKERS}; pnl = 0.0; open_ = []   # [ticker, side, q, px, i0, exit]
    def close(lot, i):
        t, sd, q, px = lot[:4]; val = 0.0
        for n in range(math.ceil(q / RATE)):
            qq = min(RATE, q - n * RATE); j = i + 1 + n
            val += qq * sd * ((mid[t][j] - sd * COST) - px) - qq * FEE if j <= 418 else qq * sd * (last[t][419] - px)
        return val
    def exit_of(t, sd, i0):
        for j in range(i0, 420, C):
            if j - L >= 0 and sd * (mid[t][j] - mid[t][j - L]) < 0: return j
        return None
    for d in sorted(tenders, key=lambda d: d["tick"]):
        i = d["tick"]
        for lot in [l for l in open_ if l[5] is not None and l[5] <= i]:
            pos[lot[0]] -= lot[1] * lot[2]; pnl += close(lot, lot[5]); open_.remove(lot)
        t, sd = d["ticker"], (1 if d["action"] == "BUY" else -1)
        b, a = d.get("bid"), d.get("ask")
        if not b or not a: continue
        edge = (b - d["price"]) if sd > 0 else (d["price"] - a)
        if not ((touch and edge >= 0) or d["tid"] in own): continue
        q = int(d["qty"])
        def fits(p):
            n = dict(p); n[t] += sd * q
            return sum(abs(v) for v in n.values()) <= B.GROSS and abs(sum(n.values())) <= B.NET
        if not fits(pos):
            if release is None or edge < release: continue
            for lot in sorted(open_, key=lambda l: l[4]):
                if fits(pos): break
                pos[lot[0]] -= lot[1] * lot[2]; pnl += close(lot, i); open_.remove(lot)
            if not fits(pos): continue
        pos[t] += sd * q
        qh = int(q * frac)
        if qh: open_.append([t, sd, qh, d["price"], i, exit_of(t, sd, i)])
        if q - qh: open_.append([t, sd, q - qh, d["price"], i, i])
    for lot in open_:
        pnl += close(lot, lot[5]) if lot[5] is not None else lot[2] * lot[1] * (last[lot[0]][419] - lot[3])
    return pnl
V = {"base": {}, "C5": dict(C=5), "L5C5": dict(L=5, C=5), "own": dict(touch=False), "rel0.10": dict(release=0.10), "rel0.20": dict(release=0.20), "half": dict(frac=0.5), "now": dict(frac=0.0)}
rows = []
for f in sorted(glob.glob("logs/data/liability-*.jsonl")):
    heat = os.path.basename(f)[10:25]
    mid, last, ten, nlv, prof, acc = B.load(f)
    if mid["CRZY"][419] is None or nlv is None: continue
    own = set()
    for l in open(f):
        d = json.loads(l)
        if d["k"] == "tender_decision" and d["accept"] and not str(d.get("reason", "")).startswith("touch edge") \
                and "MODEL" not in str(d.get("reason", "")).upper():
            own.add(d["tid"])
    rows.append(dict(heat=heat, **{k: run(mid, last, ten, own, **kw) for k, kw in V.items()}))
def dist(k):
    x = sorted(r[k] for r in rows)
    return f"mean {st.mean(x):>8,.0f} median {st.median(x):>8,.0f} worst10% {x[len(x)//10]:>8,.0f} >=70k {sum(v >= 70000 for v in x)}/{len(x)}"
def T(x): return st.mean(x) / (st.stdev(x) / math.sqrt(len(x))) if st.stdev(x) else float("nan")
print(f"{len(rows)} heats; base (rsi_bot) mean {st.mean(r['base'] for r in rows):,.0f}")
for k in list(V)[1:]:
    d = [r[k] - r["base"] for r in rows]
    e = [r[k] - r["base"] for r in rows if r["heat"] < "20261017-220000"]
    l = [r[k] - r["base"] for r in rows if r["heat"] >= "20261017-220000"]
    print(f"  {k:8} vs base {st.mean(d):>+8,.0f}/heat (t {T(d):+.1f}) better {sum(x > 0 for x in d):3}/{len(d)} worse {sum(x < 0 for x in d):3} worst {min(d):+9,.0f}"
          f" | before 10-17 22h {st.mean(e):>+8,.0f} (t {T(e):+.1f}) | ~6-person sessions {st.mean(l):>+8,.0f} (t {T(l):+.1f}, n {len(l)})")
for k in ("now", "half", "base"):
    print(f"  {k:5} {dist(k)}")
