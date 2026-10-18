"""Colleague's technique on every recorded real heat: accept fixed tenders priced better than the market, HOLD, and
exit on an RSI(10) signal (10-tick candles, as in the RIT chart) instead of unwinding at once.

Usage: python docs/results/rsi_hold_2026-10-17/backtest.py [logs/data] [--cost 0.06]

Per heat, on the recorded mids (our own / rivals' impact is baked into the path - see caveats in report.txt):
  accept  : a fixed tender whose price beats the touch by >= EDGE (BUY tender: bid - price; SELL: price - ask),
            and only if the case limits allow (gross 250k, net 100k).
  exits   : NOW    = unwind at once (mid at the tender tick, minus COST/sh)       <- what our bot roughly does
            END    = hold to the bell, closed at the last price for free (case rule)
            RSI    = long exits when RSI >= 70, short when RSI <= 30 (checked at each candle close), minus COST/sh;
                     never triggered -> closed at the bell
            Tn     = hold n ticks, minus COST/sh
Value per tender = qty * (exit price - tender price) * side; fees ($0.02/sh on the exit) included except at the bell.
"""
import glob
import json
import math
import statistics as st
import sys
from collections import defaultdict

TICKERS = ("CRZY", "TAME", "CROC")
GROSS, NET = 250_000, 100_000
FEE = 0.02
PRED = {}  # ML hold: ticker -> {tick: predicted 10-tick mid move} for the heat being run (set by the caller)
RATE = 0  # shares/tick for exits; 0 = instant at the exit tick's mid (set by --rate)


def load(path):
    mid = {t: [None] * 420 for t in TICKERS}
    last = {t: [None] * 420 for t in TICKERS}
    tenders, nlv, profile, accepted = [], None, None, set()
    for line in open(path):
        d = json.loads(line)
        k = d["k"]
        if k == "tick":
            i = d["tick"]
            if 0 <= i < 420:
                for t in TICKERS:
                    b, a = d["bid"][t], d["ask"][t]
                    if b and a:
                        mid[t][i] = (b + a) / 2
                    if d["last"][t]:
                        last[t][i] = d["last"][t]
        elif k == "tender_seen" and d.get("fixed") and d.get("price"):
            tenders.append(d)
        elif k == "tender_decision" and d.get("accept"):
            accepted.add(d["tid"])
        elif k == "summary":
            nlv, profile = d.get("nlv"), d.get("profile")
    for t in TICKERS:  # forward/back fill
        for arr in (mid[t], last[t]):
            prev = None
            for i in range(420):
                if arr[i] is None:
                    arr[i] = prev
                prev = arr[i]
            nxt = None
            for i in range(419, -1, -1):
                if arr[i] is None:
                    arr[i] = nxt
                nxt = arr[i]
    return mid, last, tenders, nlv, profile, accepted


def rsi_series(prices, candle=10, n=10):
    """RSI(n) of candle closes, Wilder smoothing; value known at the close of each candle (tick index)."""
    closes = [(i, prices[i]) for i in range(candle - 1, 420, candle)]
    out = {}
    gains, losses, ag, al = [], [], None, None
    for j in range(1, len(closes)):
        ch = closes[j][1] - closes[j - 1][1]
        g, l_ = max(ch, 0), max(-ch, 0)
        if ag is None:
            gains.append(g), losses.append(l_)
            if len(gains) == n:
                ag, al = sum(gains) / n, sum(losses) / n
            else:  # RIT shows an RSI from the first candles too: simple average so far
                a_g, a_l = sum(gains) / len(gains), sum(losses) / len(losses)
                out[closes[j][0]] = 100 if a_l == 0 else 100 - 100 / (1 + a_g / a_l)
                continue
        else:
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + l_) / n
        out[closes[j][0]] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def run_heat(mid, last, tenders, edge_min, exit_rule, cost, only=None, hi=70, lo=30):
    rsi = {t: rsi_series(mid[t]) for t in TICKERS}
    pos = {t: 0 for t in TICKERS}
    open_lots = []  # (ticker, side, qty, price, start_tick)
    pnl = 0.0
    taken = 0
    events = sorted(tenders, key=lambda d: d["tick"])

    def close_lot(lot, i, at_bell=False):
        t, side, q, px, _ = lot
        if at_bell:
            ex = last[t][419]
            return q * side * (ex - px)
        if RATE <= 0:
            ex = mid[t][i] - side * cost
            return q * side * (ex - px) - q * FEE
        # sell RATE shares/tick from the next tick on, each at that tick's recorded mid - cost; past the bell: last price
        k = math.ceil(q / RATE)
        val = 0.0
        for n in range(k):
            qq = min(RATE, q - n * RATE)
            j = i + 1 + n
            val += qq * side * ((mid[t][j] - side * cost) - px) - qq * FEE if j <= 418 else qq * side * (last[t][419] - px)
        return val

    def exit_tick(lot):
        t, side, q, px, i0 = lot
        if exit_rule == "NOW":
            return i0
        if exit_rule == "END":
            return None
        if exit_rule.startswith("T"):
            j = i0 + int(exit_rule[1:])
            return j if j < 419 else None
        if exit_rule == "RSI":
            for j in sorted(rsi[t]):
                if j <= i0:
                    continue
                r = rsi[t][j]
                if (side > 0 and r >= hi) or (side < 0 and r <= lo):
                    return j
            return None
        if exit_rule.startswith("RSI50"):  # trend: hold while RSI is on our side of `mid_`, exit at the first close
            mid_ = int(exit_rule[5:] or 50)       # that is not (checked at the accept tick too)
            for j in sorted(rsi[t]):
                if j < i0 - 9:
                    continue
                r = rsi[t][j]
                if (side > 0 and r < mid_) or (side < 0 and r > 100 - mid_):
                    return max(j, i0)
            return None
        if exit_rule.startswith("MLT"):   # ML hold: hold while the model predicts the next 10 ticks our way; START only
            th = float(exit_rule[3:])     # if the prediction is >= th our way; ticks without a prediction: keep holding
            for j in range(i0, 420, 10):
                p = PRED.get(t, {}).get(j)
                if p is not None and side * p < (th if j == i0 else 0.0):
                    return j
            return None
        if exit_rule.startswith("MOMT"):  # MOM10, but START the hold only if the last 10 ticks moved >= th our way
            th = float(exit_rule[4:])
            for j in range(i0, 420, 10):
                if j - 10 >= 0:
                    run = side * (mid[t][j] - mid[t][j - 10])
                    if run < (th if j == i0 else 0.0):
                        return j
            return None
        if exit_rule.startswith("STOP"):  # MOM10 + trailing stop: also exit the first tick the mid gives back > g from
            g = float(exit_rule[4:])      # its best (our way) since the hold began
            best = side * mid[t][i0]
            for j in range(i0, 420):
                if (j - i0) % 10 == 0 and j - 10 >= 0 and side * (mid[t][j] - mid[t][j - 10]) < 0:
                    return j
                best = max(best, side * mid[t][j])
                if best - side * mid[t][j] > g:
                    return j
            return None
        if exit_rule.startswith("MOM"):  # trend: hold while the last L-tick move is our way (checked every 10 ticks)
            L = int(exit_rule[3:])
            for j in range(i0, 420, 10):
                if j - L >= 0 and side * (mid[t][j] - mid[t][j - L]) < 0:
                    return j
            return None
        raise ValueError(exit_rule)

    scheduled = []  # (exit tick or None, lot)
    for d in events:
        i = d["tick"]
        # release lots that exited before this tender
        still = []
        for ex, lot in scheduled:
            if ex is not None and ex <= i:
                pos[lot[0]] -= lot[1] * lot[2]
                pnl += close_lot(lot, ex)
            else:
                still.append((ex, lot))
        scheduled = still
        if only is not None and d["tid"] not in only:
            continue
        t = d["ticker"]
        side = 1 if d["action"] == "BUY" else -1  # BUY tender: we buy from the institution
        b, a = d.get("bid"), d.get("ask")
        if not b or not a:
            continue
        edge = (b - d["price"]) if side > 0 else (d["price"] - a)
        if edge < edge_min:
            continue
        q = int(d["qty"])
        newpos = dict(pos)
        newpos[t] += side * q
        if sum(abs(v) for v in newpos.values()) > GROSS or abs(sum(newpos.values())) > NET:
            continue
        pos = newpos
        taken += 1
        lot = (t, side, q, d["price"], i)
        scheduled.append((exit_tick(lot), lot))
    for ex, lot in scheduled:
        pnl += close_lot(lot, ex) if ex is not None else close_lot(lot, 419, at_bell=True)
    return pnl, taken


def tstat(xs):
    if len(xs) < 2:
        return float("nan")
    s = st.stdev(xs)
    return st.mean(xs) / (s / math.sqrt(len(xs))) if s > 0 else float("nan")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = args[0] if args else "logs/data"
    cost = 0.06
    if "--cost" in sys.argv:
        cost = float(sys.argv[sys.argv.index("--cost") + 1])
    global RATE
    if "--rate" in sys.argv:
        RATE = int(sys.argv[sys.argv.index("--rate") + 1])
    crowded = set()
    if "--crowded" in sys.argv:
        crowded = set(open(sys.argv[sys.argv.index("--crowded") + 1]).read().split())
    heats = []
    for f in sorted(glob.glob(f"{root}/liability-*.jsonl")):
        mid, last, tenders, nlv, profile, accepted = load(f)
        if mid["CRZY"][419] is None or nlv is None:
            continue
        name = f.split("liability-")[1][:15]
        heats.append((name, mid, last, tenders, nlv, accepted))
    print(f"{len(heats)} heats with full paths, cost {cost:.2f}/sh on any exit before the bell, exit rate "
          f"{RATE or 'instant'} sh/tick, crowded heats {sum(h[0] in crowded for h in heats)}\n")

    rules = [r for r in ["NOW", "T50", "T100", "RSI", "END", "RSI50", "RSI5060", "MOM10", "MOM20", "MOM50"]
             if "--rules" not in sys.argv or r in sys.argv[sys.argv.index("--rules") + 1].split(",")]
    for label, edge_min, use_bot in (("bot's own accepts", None, True),
                                     ("raw edge >= 0.00", 0.0, False),
                                     ("raw edge >= 0.10", 0.10, False)):
        res = defaultdict(list)
        ntaken = []
        for name, mid, last, tenders, nlv, accepted in heats:
            for r in rules:
                p, n = run_heat(mid, last, tenders, -9 if use_bot else edge_min, r, cost,
                                only=accepted if use_bot else None)
                res[r].append(p)
                if r == "NOW":
                    ntaken.append(n)
        print(f"== accept: {label}  (fixed tenders taken/heat {st.mean(ntaken):.1f})")
        base = res["NOW"]
        print(f"  {'exit':6} {'mean':>9} {'median':>9} {'worst':>9} {'best':>9} {'sd':>8}  {'vs NOW':>8} {'t':>5}  "
              f"{'early half':>10} {'late half':>10}  {'10-17 night':>11} {'crowded':>8}")
        half = len(heats) // 2
        night = [i for i, h in enumerate(heats) if h[0] >= "20261017-220000"]
        for r in rules:
            xs = res[r]
            diff = [x - y for x, y in zip(xs, base)]
            print(f"  {r:6} {st.mean(xs):>9,.0f} {st.median(xs):>9,.0f} {min(xs):>9,.0f} {max(xs):>9,.0f} "
                  f"{st.stdev(xs):>8,.0f}  {st.mean(diff):>+8,.0f} {tstat(diff):>5.1f}  "
                  f"{st.mean(diff[:half]):>+10,.0f} {st.mean(diff[half:]):>+10,.0f}  "
                  f"{st.mean([diff[i] for i in night]):>+11,.0f} "
                  f"{st.mean([diff[i] for i, h in enumerate(heats) if h[0] in crowded] or [0]):>+8,.0f}")
        print()

    # the heat in the colleague's screenshot
    for name, mid, last, tenders, nlv, accepted in heats:
        if name == "20261017-231558":
            print("Screenshot heat 20261017-231558 (our live NLV {:,.0f}):".format(nlv))
            for r in rules:
                p, n = run_heat(mid, last, tenders, 0.0, r, cost)
                print(f"  raw edge >= 0, exit {r:5}: {p:>10,.0f}  ({n} tenders)")


if __name__ == "__main__":
    main()
