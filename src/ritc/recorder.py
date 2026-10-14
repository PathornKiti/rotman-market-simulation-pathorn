"""
Practice-server recorder and calibrator (Liquidity Risk Case).

    python -m ritc record                       # READ-ONLY: polls the API, writes logs/record-<time>.jsonl
    python -m ritc calibrate logs/record-*.jsonl # what the market really looks like, vs our simulator

The brief gives only "High" volatility, "Medium-low" liquidity, "random intervals". The
simulator's numbers for those are guesses, and every tuned setting rests on them. A practice
heat recorded with this tool replaces the guesses with measurements:

* price volatility per tick, spread, and depth within 5/10/25 cents of the touch, per stock,
  over time (CROC "varied") and in the last 30 s (the brief: market makers add liquidity);
* tenders: how many, how often, size, side, fixed price vs auction, caption wording, the
  tender's edge vs the mid when it arrived, and how long its decision window is;
* how long a tender stays listed vs its `expires`: does an auction we bid on stay listed
  (pending until expiry) or vanish at once? This decides the front-running question;
* booking delay: ticks between an accepted tender leaving the list and our position jumping;
* crowd event study: after each tender arrives, and after its window closes, how far does the mid run
  AGAINST the direction its holders must unwind, per 10k shares? If other desks get the same block, this
  is where it shows, and WHEN (on arrival or at expiry) decides whether answering late protects us;
* fill model (time and sales): how far beyond the mid do trades reach each tick? An order resting at
  distance d from the mid can only fill when trades reach d, so P(reach d) ~ A exp(-kappa d) is the
  fill-rate curve that decides WHERE to rest the unwind (Cartea & Jaimungal style). Ticks where we traded
  ourselves are left out (our own sweeps are not the market's);
* order-book imbalance: does a heavy bid (ask) side, or the micro-price, predict the mid's next
  1 and 5 ticks? Our simulator draws book sizes at random, so only a real recording can say. If it
  does, the unwind could cross when the next move is against it and rest when it is for it.

The recorder only GETs, so it is safe to run next to the bot, or while trading by hand.
"""

from __future__ import annotations

import json
import math
import statistics as st
import time
from pathlib import Path

from .core.client import RITClient, RITError
from .pricing.timeseries import volatility_clusters


def snapshots(interval: float = 0.5, depth: int = 20, stop=None, quiet: bool = False):
    """
    Yield one market snapshot every `interval` s (case, trader, securities, tenders, limits, books, NEW time and
    sales) until the case stops after being ACTIVE, the API is gone, or `stop` (a threading.Event) is set.
    """
    c = RITClient(max_retries=1)
    last_tick, seen_active, errors = None, False, 0
    tas_after: dict[str, int] = {}
    while not (stop is not None and stop.is_set()):
        t0 = time.time()
        try:
            case = c.case()
            snap = {"t": round(t0, 3), "case": case, "trader": c.trader(),
                    "securities": c.securities(), "tenders": c.tenders(), "limits": c.limits()}
            stocks = [s["ticker"] for s in snap["securities"] if s.get("type", "STOCK") == "STOCK"]
            snap["books"] = {t: c.book(t, depth) for t in stocks}
            snap["tas"] = {}
            for t in stocks:                     # only trades we have not stored yet
                rows = c.tas(t, after=tas_after.get(t))
                rows = [r for r in rows if int(r.get("id", 0)) > tas_after.get(t, -1)]
                if rows:
                    tas_after[t] = max(int(r["id"]) for r in rows)
                    snap["tas"][t] = rows
        except RITError as exc:
            errors += 1
            if not quiet:
                print(f"API error: {exc}")
            if seen_active and errors >= 5:          # the case (or the client) is gone
                return
            time.sleep(1.0)
            continue
        errors = 0
        yield snap
        status, tick = case.get("status"), case.get("tick")
        if status == "ACTIVE":
            seen_active = True
        elif seen_active:
            if not quiet:
                print("case stopped")
            return
        if not quiet and tick != last_tick and tick is not None and tick % 30 == 0:
            print(f"tick {tick}  tenders listed {len(snap['tenders'])}")
        last_tick = tick
        time.sleep(max(0.0, interval - (time.time() - t0)))


def record(path: str | None = None, interval: float = 0.5, depth: int = 20) -> str:
    out = Path(path or Path("logs") / f"record-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"recording to {out} (Ctrl+C to stop; stops by itself when the case ends)")
    with out.open("w") as f:
        try:
            for snap in snapshots(interval, depth):
                f.write(json.dumps(snap) + "\n")
        except KeyboardInterrupt:
            pass
    return str(out)


def record_into(journal, stop, interval: float = 0.5, depth: int = 20) -> None:
    """
    `ritc run --record`: the same snapshots as `ritc record`, written into the bot's own journal as
    {"k": "market", ...} rows (ACTIVE heat only), so one heat = one .log + one .jsonl. Runs in a thread
    until `stop` is set; never raises (a recorder problem must not touch the trading bot).
    """
    import logging
    try:
        for snap in snapshots(interval, depth, stop=stop, quiet=True):
            if snap["case"].get("status") == "ACTIVE":
                journal.write("market", **snap)
    except Exception as exc:                     # noqa: BLE001
        logging.getLogger("ritc").warning("recorder stopped: %s", exc)


def load_snapshots(path: str) -> list[dict]:
    """Snapshots from a `ritc record` file, or the `market` rows of a bot journal (`ritc run --record`)."""
    out = []
    for line in Path(path).read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue                              # a line cut off by a crash
        if "k" in r:
            if r["k"] != "market":
                continue
            r = {k: v for k, v in r.items() if k not in ("k", "w")}
        if "case" in r:
            out.append(r)
    return out


def _levels(side: list[dict]) -> list[tuple[float, int]]:
    return [(float(r["price"]), int(r.get("quantity", 0)) - int(r.get("quantity_filled", 0) or 0))
            for r in side if r.get("price") is not None]


def _depth_within(levels: list[tuple[float, int]], touch: float, cents: float) -> int:
    return sum(q for p, q in levels if abs(p - touch) <= cents + 1e-9)


def calibrate(path: str) -> dict:
    rows = [r for r in load_snapshots(path) if r["case"].get("status") == "ACTIVE"]
    if not rows:
        raise SystemExit("no ACTIVE snapshots in the recording")
    tpp = int(rows[0]["case"].get("ticks_per_period") or 420)
    by_tick: dict[int, dict] = {}
    for r in rows:
        by_tick.setdefault(int(r["case"]["tick"]), r)          # first snapshot of each tick
    ticks = sorted(by_tick)
    tickers = sorted(by_tick[ticks[0]]["books"])
    rep: dict = {"file": path, "ticks": [ticks[0], ticks[-1]], "ticks_per_period": tpp, "stocks": {}}

    # ---------------- market, per stock
    for tk in tickers:
        mids, spreads, d5, d10, d25, end10 = [], [], [], [], [], []
        for k in ticks:
            b = by_tick[k]["books"].get(tk) or {}
            bids, asks = _levels(b.get("bids", b.get("bid", []))), _levels(b.get("asks", b.get("ask", [])))
            if not bids or not asks:
                continue
            bb, ba = max(p for p, _ in bids), min(p for p, _ in asks)
            mids.append((k, (bb + ba) / 2))
            spreads.append(ba - bb)
            for out, c in ((d5, 0.05), (d10, 0.10), (d25, 0.25)):
                out.append((_depth_within(bids, bb, c) + _depth_within(asks, ba, c)) / 2)
            if tpp - k <= 30:
                end10.append((_depth_within(bids, bb, 0.10) + _depth_within(asks, ba, 0.10)) / 2)
        if len(mids) < 10:
            continue
        diffs = [(m2 - m1) / max(1, k2 - k1) ** 0.5 for (k1, m1), (k2, m2) in zip(mids, mids[1:])]
        thirds = [d10[i * len(d10) // 3:(i + 1) * len(d10) // 3] for i in range(3)]
        rep["stocks"][tk] = {
            "start_mid": round(mids[0][1], 2), "end_mid": round(mids[-1][1], 2),
            "sigma_per_tick": round(st.pstdev(diffs), 4),
            "sigma_pct_per_tick": round(100 * st.pstdev(diffs) / st.fmean(m for _, m in mids), 3),
            # ARCH test on squared moves: do calm / turbulent spells exist (what a vol-regime model needs)?
            "vol_clusters_q": round(volatility_clusters(diffs, 5)[1], 1),
            "spread_median": round(st.median(spreads), 3),
            "depth_5c": round(st.median(d5)), "depth_10c": round(st.median(d10)), "depth_25c": round(st.median(d25)),
            "depth_10c_by_third": [round(st.median(x)) if x else None for x in thirds],
            "depth_10c_last30s": round(st.median(end10)) if end10 else None,
        }

    # ---------------- tenders
    life: dict[int, dict] = {}
    for k in ticks:
        r = by_tick[k]
        mid = {tk: v["start_mid"] for tk, v in rep["stocks"].items()}
        for tk in tickers:
            b = r["books"].get(tk) or {}
            bids, asks = _levels(b.get("bids", b.get("bid", []))), _levels(b.get("asks", b.get("ask", [])))
            if bids and asks:
                mid[tk] = (max(p for p, _ in bids) + min(p for p, _ in asks)) / 2
        for t in r["tenders"]:
            tid = int(t["tender_id"])
            if tid not in life:
                m = mid.get(t.get("ticker"))
                sign = 1 if str(t.get("action")).upper() == "BUY" else -1
                life[tid] = {**t, "first_seen": k, "last_seen": k, "mid_at_arrival": m,
                             # $/share in OUR favour vs the mid (BUY: we buy below the mid)
                             "edge_vs_mid": (round(sign * (m - t["price"]), 3)
                                             if m is not None and t.get("price") is not None else None)}
            life[tid]["last_seen"] = k
    ts = sorted(life.values(), key=lambda x: x["first_seen"])
    arrivals = [x["first_seen"] for x in ts]
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    edges = [x["edge_vs_mid"] for x in ts if x["edge_vs_mid"] is not None]
    rep["tenders"] = {
        "count": len(ts),
        "mean_gap_ticks": round(st.fmean(gaps), 1) if gaps else None,
        "per_ticker": {tk: sum(x.get("ticker") == tk for x in ts) for tk in tickers},
        "fixed_vs_auction": [sum(bool(x.get("is_fixed_bid")) for x in ts), sum(not x.get("is_fixed_bid") for x in ts)],
        "sizes": sorted({int(x["quantity"]) for x in ts}),
        "window_ticks": sorted({int(x["expires"]) - int(x["tick"]) for x in ts if x.get("expires") is not None}),
        "edge_vs_mid": ({"min": min(edges), "median": st.median(edges), "max": max(edges)} if edges else None),
        "captions": sorted({x.get("caption", "") for x in ts})[:12],
        # Listed until its expiry, or gone early (answered: accepted / declined / bid)?
        "gone_before_expiry": [x["tender_id"] for x in ts
                               if x.get("expires") is not None and x["last_seen"] < int(x["expires"]) - 1],
        "list": [{k: x.get(k) for k in ("tender_id", "ticker", "action", "quantity", "price", "is_fixed_bid",
                                         "tick", "expires", "first_seen", "last_seen", "edge_vs_mid", "caption")}
                 for x in ts],
    }

    # ---------------- booking delay: tender leaves the list -> position jumps by its size
    pos = {k: {s["ticker"]: int(s.get("position", 0)) for s in by_tick[k]["securities"]} for k in ticks}
    delays = []
    for x in ts:
        gone, tk, q = x["last_seen"] + 1, x.get("ticker"), int(x["quantity"])
        sign = 1 if str(x.get("action")).upper() == "BUY" else -1
        before = pos.get(x["last_seen"], {}).get(tk)
        for k in ticks:
            if k < gone or k > gone + 35 or before is None:
                continue
            if sign * (pos[k].get(tk, 0) - before) >= 0.8 * q:
                delays.append({"tender_id": x["tender_id"], "booked_ticks_after_leaving_list": k - x["last_seen"]})
                break
    rep["accepted_tender_bookings"] = delays
    me = rows[-1]["trader"].get("trader_id")
    rep["imbalance"] = {tk: imbalance_signal(by_tick, ticks, tk, me) for tk in tickers}
    booked = {d["tender_id"] for d in delays}            # ours: our own unwind would look like a crowd
    rep["crowd_raw"] = crowd_event_study(by_tick, ticks, [x for x in ts if x["tender_id"] not in booked])
    rep["crowd"] = summarise_crowd(rep["crowd_raw"])
    rep["fills"] = {tk: fill_model(rows, by_tick, ticks, tk) for tk in tickers}
    rep["flow"] = {tk: flow_persistence(rows, by_tick, ticks, tk) for tk in tickers}
    rep["crowd_excluded_own"] = sorted(booked)
    rep["root_cause_raw"] = root_cause_samples(by_tick, ticks, tickers, me)
    rep["root_cause"] = root_cause_verdict(rep["root_cause_raw"])
    fin = rows[-1]["trader"]
    rep["final_nlv"] = fin.get("nlv")
    rep["limits"] = rows[-1]["limits"]
    return rep


def _others_mid(by_tick: dict, k: int, tk: str, me=None) -> float | None:
    """Mid of the book WITHOUT our own orders (ours sit at the touch while we unwind and would move it)."""
    b = by_tick[k]["books"].get(tk) or {}
    side = {n: [float(r["price"]) for r in b.get(n + "s", b.get(n, [])) if r.get("price") is not None
                and int(r.get("quantity", 0)) - int(r.get("quantity_filled", 0) or 0) > 0
                and (me is None or r.get("trader_id") != me)] for n in ("bid", "ask")}
    return (max(side["bid"]) + min(side["ask"])) / 2 if side["bid"] and side["ask"] else None


def root_cause_samples(by_tick: dict, ticks: list[int], tickers: list[str], me=None, w: int = 10) -> dict:
    """
    ROOT-CAUSE TEST for the losing unwinds (solo practice 2026-10-13: the mid ran against every block we held).
    Two explanations look the same in block summaries and need opposite fixes:
      TREND   the price trends on its own; our resting side stops filling, so we are still holding during trends.
              Then the price trends while we are FLAT too (momentum: last w ticks predict the next w), and the move
              against a fresh block is ~0 at first and only shows in blocks that stalled.
      HOLDER  holding itself is what moves the price. Then there is no momentum while flat, and the move against
              us starts the moment we hold (age 0-10 ticks) at about the same rate as later.
      INVENTORY  our own fills move the price (the market maker skews after absorbing them). Looks like HOLDER in
              this test (validated on the simulator, 7 heats each): only `execution.hold_test_ticks` separates them.
              Per-tick fill splits and block move vs duration were tried and do not work: resting orders fill
              when the price comes to them, which biases both.
    Raw samples (pooled across recordings by `ritc calibrate`): `flat` = (move over the previous w ticks, move over
    the next w) with our position 0 throughout; `age` buckets = the mid's 1-tick move against the position we held
    over that tick, by ticks since that position was opened from flat.
    """
    out: dict = {"flat": [], "hold": [], "age": {"0-10": [], "10-30": [], "30+": []}}
    for tk in tickers:
        mids = {k: _others_mid(by_tick, k, tk, me) for k in ticks}
        pos = {k: next((int(s.get("position", 0)) for s in by_tick[k]["securities"] if s["ticker"] == tk), 0)
               for k in ticks}
        opened = None
        for k in ticks:
            q = pos[k]
            if q == 0:
                opened = None
            elif opened is None:
                opened = k
            k1 = k + 1
            if q and k1 in mids and mids[k] is not None and mids[k1] is not None:
                x = (-1 if q > 0 else 1) * (mids[k1] - mids[k])
                age = k - opened
                out["age"]["0-10" if age < 10 else "10-30" if age < 30 else "30+"].append(round(x, 4))
            a, b, c = k - w, k, k + w
            if all(j in mids and mids[j] is not None for j in (a, b, c)):
                if all(pos.get(j, 1) == 0 for j in range(a, c + 1)):
                    out["flat"].append((round(mids[b] - mids[a], 4), round(mids[c] - mids[b], 4)))
                elif all(pos.get(j, 0) and (pos[j] > 0) == (q > 0) for j in range(a, c + 1)) and q:
                    sg = -1 if q > 0 else 1                  # signed so + = against what we hold
                    out["hold"].append((round(sg * (mids[b] - mids[a]), 4), round(sg * (mids[c] - mids[b]), 4)))
    return out


def root_cause_verdict(raw: dict, w: int = 10) -> dict:
    """Summary of `root_cause_samples` (one recording or pooled): momentum while flat and while holding (every w-th
    pair, non-overlapping), and the mean move against us per 30 ticks by position age."""
    def mom(pairs):
        pairs = pairs[::w]
        if len(pairs) < 8:
            return {"n": len(pairs)}
        r, t = _corr([a for a, _ in pairs], [b for _, b in pairs])
        return {"n": len(pairs), "r": round(r, 3), "t": round(t, 1)}
    ages = {}
    for k, v in raw["age"].items():
        if len(v) >= 10:
            m, sd = st.fmean(v), st.pstdev(v)
            ages[k] = {"n": len(v), "per_30": round(30 * m, 3), "t": round(m / (sd / len(v) ** 0.5), 1) if sd else 0.0}
        else:
            ages[k] = {"n": len(v)}
    return {"flat_momentum": mom(raw["flat"]), "hold_momentum": mom(raw["hold"]), "against_by_age": ages}


def print_root_cause(v: dict) -> None:
    f, h, a = v["flat_momentum"], v["hold_momentum"], v["against_by_age"]
    print("\nROOT-CAUSE TEST (why unwinds lose): TREND = momentum while FLAT (r > 0, t >= 2) and little move against a "
          "fresh block; HOLDER = no momentum while flat and the move against us already there at age 0-10 ticks")
    print(f"  momentum (last 10 ticks -> next 10) while flat: {f}   while holding: {h}")
    print("  move against our position, $ per 30 ticks, by ticks since it opened: "
          + "  ".join(f"{k}: {x}" for k, x in a.items()))
    print("  HOLDER vs INVENTORY (our own fills) cannot be told apart from a passive unwind: run one heat with "
          "execution.hold_test_ticks (HOLD TEST lines in the bot log)")


def _corr(x: list[float], y: list[float]) -> tuple[float, float]:
    """Pearson r and its t statistic (0, 0 if undefined)."""
    n = len(x)
    if n < 10 or st.pstdev(x) == 0 or st.pstdev(y) == 0:
        return 0.0, 0.0
    r = st.correlation(x, y)
    return r, r * ((n - 2) / max(1e-12, 1 - r * r)) ** 0.5


def imbalance_signal(by_tick: dict, ticks: list[int], tk: str, me=None) -> dict:
    """
    Predictive power of the book for the next mid move, from OTHER traders' orders only (ours are
    excluded: they would predict our own fills). Horizons 1 and 5 ticks, non-overlapping samples so
    the t statistics are honest. obi1 = top level, obi5 = top 5 levels, micro = micro-price - mid.
    """
    feats: dict[int, tuple[float, float, float, float]] = {}
    for k in ticks:
        b = by_tick[k]["books"].get(tk) or {}
        side = {n: [(float(r["price"]), int(r.get("quantity", 0)) - int(r.get("quantity_filled", 0) or 0))
                    for r in b.get(n + "s", b.get(n, [])) if r.get("price") is not None
                    and (me is None or r.get("trader_id") != me)] for n in ("bid", "ask")}
        bids = sorted(side["bid"], reverse=True)
        asks = sorted(side["ask"])
        if not bids or not asks:
            continue
        def agg(levels):                                   # aggregate by price, best first
            out: dict[float, int] = {}
            for p, q in levels:
                out[p] = out.get(p, 0) + q
            return list(out.items())
        ab, aa = agg(bids), agg(asks)
        (pb, vb), (pa, va) = ab[0], aa[0]
        if vb + va <= 0:
            continue
        mid = (pb + pa) / 2
        v5b, v5a = sum(q for _, q in ab[:5]), sum(q for _, q in aa[:5])
        feats[k] = (mid, (vb - va) / (vb + va), (v5b - v5a) / max(1, v5b + v5a),
                    (pa * vb + pb * va) / (vb + va) - mid)
    out = {}
    for h in (1, 5):
        xs1, xs5, xm, ys = [], [], [], []
        for k in ticks[::h]:
            if k in feats and k + h in feats:
                m, o1, o5, mc = feats[k]
                xs1.append(o1)
                xs5.append(o5)
                xm.append(mc)
                ys.append(feats[k + h][0] - m)
        r1, t1 = _corr(xs1, ys)
        r5, t5 = _corr(xs5, ys)
        rm, tm = _corr(xm, ys)
        hit = (sum((a > 0) == (b > 0) for a, b in zip(xs1, ys) if a and b) /
               max(1, sum(1 for a, b in zip(xs1, ys) if a and b)))
        out[f"h{h}"] = {"n": len(ys), "obi1_r": round(r1, 3), "obi1_t": round(t1, 1), "obi5_r": round(r5, 3),
                        "obi5_t": round(t5, 1), "micro_r": round(rm, 3), "micro_t": round(tm, 1),
                        "obi1_sign_hit": round(hit, 3)}
    return out


def flow_persistence(rows: list[dict], by_tick: dict, ticks: list[int], tk: str, w: int = 10) -> dict:
    """
    The two numbers that decide whether a VPIN / order-flow "cross when the flow turns against us" rule can pay
    (docs/RESEARCH.md, 2026-10-12): over non-overlapping `w`-tick windows, does the signed trade flow (volume
    above the mid - below it, / total) predict the NEXT window's mid move (r, t), and do prices trend (variance
    ratio of w-tick to 1-tick moves; 1 = random walk, > 1 = momentum)? Simulator: no coupling r ~ 0, VR ~ 1;
    persistent Kyle flow (kyle=0.5, kyle_phi=0.9) r ~ +0.2, VR ~ 1.5, and the rule still did not pay there.
    """
    mids: dict[int, float] = {}
    for k in ticks:
        b = by_tick[k]["books"].get(tk) or {}
        bids, asks = _levels(b.get("bids", b.get("bid", []))), _levels(b.get("asks", b.get("ask", [])))
        if bids and asks:
            mids[k] = (max(p for p, _ in bids) + min(p for p, _ in asks)) / 2
    signed: dict[int, list[float]] = {}
    for r in rows:
        for x in (r.get("tas") or {}).get(tk, []):
            k = int(x["tick"])
            m = mids.get(k) or mids.get(k - 1)
            if m is None:
                continue
            q = float(x.get("quantity", 0))
            v = signed.setdefault(k, [0.0, 0.0])
            v[0 if float(x["price"]) >= m else 1] += q
    xs, ys = [], []
    for k in ticks[::w]:
        if k in mids and k - w in mids and k + w in mids:
            b = sum(signed.get(j, [0.0, 0.0])[0] for j in range(k - w + 1, k + 1))
            s_ = sum(signed.get(j, [0.0, 0.0])[1] for j in range(k - w + 1, k + 1))
            if b + s_ > 0:
                xs.append((b - s_) / (b + s_))
                ys.append(mids[k + w] - mids[k])
    r, t = _corr(xs, ys)
    d1 = [mids[k + 1] - mids[k] for k in ticks if k in mids and k + 1 in mids]
    dw = [mids[k + w] - mids[k] for k in ticks[::w] if k in mids and k + w in mids]
    vr = (st.pvariance(dw) / (w * st.pvariance(d1))) if len(dw) > 5 and len(d1) > 5 and st.pvariance(d1) else None
    return {"n": len(xs), "flow_next_r": round(r, 3), "flow_next_t": round(t, 1),
            "variance_ratio": round(vr, 2) if vr else None}


def crowd_event_study(by_tick: dict, ticks: list[int], tenders: list[dict], lag: int = 10) -> dict:
    """
    Signed mid move against each tender's unwind direction (+ = the price ran against whoever holds the
    block, i.e. a crowd), per 10k shares, over `lag` ticks after ARRIVAL and after EXPIRY; per stock and
    per tender type. Mean, t statistic, n. Noise is the random walk: judge |t| >= 3 across heats.
    """
    def mid(k: int, tk: str) -> float | None:
        r = by_tick.get(k)
        b = (r or {}).get("books", {}).get(tk) or {}
        bids, asks = _levels(b.get("bids", b.get("bid", []))), _levels(b.get("asks", b.get("ask", [])))
        return (max(p for p, _ in bids) + min(p for p, _ in asks)) / 2 if bids and asks else None

    rows: dict[str, dict[str, list[float]]] = {}
    for x in tenders:
        tk, q = x.get("ticker"), int(x.get("quantity", 0))
        if not tk or q <= 0:
            continue
        sign = -1 if str(x.get("action")).upper() == "BUY" else 1     # BUY tender: holders sell -> price falls
        kind = "fixed" if x.get("is_fixed_bid") else "auction"
        for when, k0 in (("arrival", x["first_seen"]), ("expiry", x.get("expires"))):
            if k0 is None:
                continue
            m0, m1 = mid(int(k0), tk), mid(int(k0) + lag, tk)
            if m0 is None or m1 is None:
                continue
            y = sign * (m1 - m0) / (q / 10_000)
            for key in (f"{tk}|{when}", f"all|{when}", f"{kind}|{when}"):
                rows.setdefault(key, {}).setdefault("y", []).append(y)
    return {key: d["y"] for key, d in rows.items()}


def fill_model(rows: list[dict], by_tick: dict, ticks: list[int], tk: str, tick_size: float = 0.01) -> dict:
    """
    From time and sales: per tick, how far beyond the mid the market's trades reached on each side
    (trade above the mid = a buyer lifted offers = a resting SELL there would have filled). P(reach d)
    for d = half a spread ... +6 ticks, and a log-linear fit P ~ A exp(-kappa d). Ticks in which our own
    position moved are excluded. Queue position is ignored: an upper bound on the real fill rate.
    """
    trades: dict[int, list[tuple[float, int]]] = {}
    for r in rows:
        for x in (r.get("tas") or {}).get(tk, []):
            trades.setdefault(int(x["tick"]), []).append((float(x["price"]), int(x.get("quantity", 0))))
    pos = {k: next((int(s.get("position", 0)) for s in by_tick[k]["securities"] if s["ticker"] == tk), 0)
           for k in ticks}
    reach_up, reach_dn, n = [], [], 0
    for k0, k1 in zip(ticks, ticks[1:]):
        if pos.get(k1) != pos.get(k0):
            continue                                   # we traded: our own sweep is not the market's
        b = by_tick[k0]["books"].get(tk) or {}
        bids, asks = _levels(b.get("bids", b.get("bid", []))), _levels(b.get("asks", b.get("ask", [])))
        if not bids or not asks:
            continue
        mid = (max(p for p, _ in bids) + min(p for p, _ in asks)) / 2
        n += 1
        tr = trades.get(k1, [])
        reach_up.append(max([p - mid for p, _ in tr if p > mid], default=0.0))
        reach_dn.append(max([mid - p for p, _ in tr if p < mid], default=0.0))
    if n < 20:
        return {"ticks": n}
    out: dict = {"ticks": n, "trades_per_tick": round(sum(len(trades.get(k, [])) for k in ticks) / len(ticks), 2)}
    grid = [tick_size * (0.5 + i) for i in range(7)]           # half a tick from the mid ... 6.5 ticks
    for side, reach in (("sell_at", reach_up), ("buy_at", reach_dn)):
        probs = [sum(r >= d - 1e-9 for r in reach) / n for d in grid]
        out[side] = {f"{d:.3f}": round(p, 3) for d, p in zip(grid, probs)}
        pts = [(d, math.log(p)) for d, p in zip(grid, probs) if p > 0]
        if len(pts) >= 3:
            md, ml = st.fmean(d for d, _ in pts), st.fmean(lp for _, lp in pts)
            sxx = sum((d - md) ** 2 for d, _ in pts)
            slope = sum((d - md) * (lp - ml) for d, lp in pts) / sxx if sxx else 0.0
            out[side + "_kappa"] = round(-slope, 1)             # per $ of distance from the mid
    return out


def summarise_crowd(raw: dict[str, list[float]]) -> dict:
    """Mean and t statistic (sample sd) of each event-study cell; pool several heats by concatenating raw lists."""
    out = {}
    for key, y in sorted(raw.items()):
        sd = st.stdev(y) if len(y) > 2 else 0.0
        out[key] = {"n": len(y), "mean_per_10k": round(st.fmean(y), 4),
                    "t": round(st.fmean(y) / (sd / len(y) ** 0.5), 1) if sd > 0 else 0.0}
    return out


def print_crowd(cells: dict) -> None:
    for key, v in cells.items():
        print(f"  {key:<18} n {v['n']:>3}  {v['mean_per_10k']:+.4f} $/10k  (t {v['t']:+.1f})")


def print_report(rep: dict) -> None:
    print(f"\n{rep['file']}: ticks {rep['ticks'][0]}-{rep['ticks'][1]} of {rep['ticks_per_period']}")
    print(f"\n{'stock':<6}{'start':>7}{'sigma/tick':>11}{'%/tick':>8}{'spread':>8}{'d5c':>8}{'d10c':>8}"
          f"{'d25c':>8}  d10c by third        last30s   vol-cluster Q (>11.1 = clusters)")
    for tk, s in rep["stocks"].items():
        print(f"{tk:<6}{s['start_mid']:>7}{s['sigma_per_tick']:>11}{s['sigma_pct_per_tick']:>8}"
              f"{s['spread_median']:>8}{s['depth_5c']:>8}{s['depth_10c']:>8}{s['depth_25c']:>8}  "
              f"{str(s['depth_10c_by_third']):<20} {str(s['depth_10c_last30s']):<9} {s['vol_clusters_q']}")
    t = rep["tenders"]
    print(f"\ntenders {t['count']}  mean gap {t['mean_gap_ticks']} ticks  per stock {t['per_ticker']}  "
          f"fixed/auction {t['fixed_vs_auction']}")
    print(f"sizes {t['sizes']}\nwindows (ticks) {t['window_ticks']}\nedge vs mid ($/share, + = in our favour) "
          f"{t['edge_vs_mid']}")
    print("captions:\n  " + "\n  ".join(t["captions"]))
    print(f"left the list before expiry (answered): {t['gone_before_expiry']}")
    print(f"booking delays: {rep['accepted_tender_bookings']}")
    print(f"limits: {[(x.get('name'), x.get('gross_limit'), x.get('net_limit')) for x in rep['limits']]}")
    print("\ncrowd event study: mid move AGAINST the holders' unwind over 10 ticks, $ per 10k shares "
          "(+ and |t| >= 3 across heats = other desks unwind the same blocks).\n  Tenders we took are left out "
          f"({len(rep.get('crowd_excluded_own', []))}); a DRY-RUN heat gives the cleanest reading.")
    print_crowd(rep.get("crowd", {}))
    print("\nfill model from time and sales: P(the market's trades reach d beyond the mid in a tick), "
          "our own ticks excluded; kappa = decay per $ (upper bound: ignores the queue)")
    for tk, f in rep.get("fills", {}).items():
        if "sell_at" not in f:
            print(f"  {tk:<6} not enough ticks ({f.get('ticks', 0)})")
            continue
        print(f"  {tk:<6} trades/tick {f['trades_per_tick']}  "
              f"kappa sell {f.get('sell_at_kappa')} buy {f.get('buy_at_kappa')}")
        for side in ("sell_at", "buy_at"):
            print(f"      {side:<8} " + "  ".join(f"{d}:{p:.2f}" for d, p in f[side].items()))
    print("\nsigned trade flow -> next 10-tick move, and variance ratio (decides the VPIN / toxic-flow rule: "
          "it needs r > 0 with t >= 3 AND VR well above 1 over 2+ heats)")
    for tk, f in rep.get("flow", {}).items():
        print(f"  {tk:<6} n {f['n']:>3}  r {f['flow_next_r']:+.3f} (t {f['flow_next_t']:+.1f})  "
              f"variance ratio {f['variance_ratio']}")
    if "root_cause" in rep:
        print_root_cause(rep["root_cause"])
    print("\nbook imbalance -> next mid move (others' orders only; |t| >= 3 on a real recording = a usable signal)")
    for tk, d in rep.get("imbalance", {}).items():
        for h, v in d.items():
            print(f"  {tk:<6}{h:<4} n {v['n']:>4}  top-level r {v['obi1_r']:+.3f} (t {v['obi1_t']:+.1f})  "
                  f"top-5 r {v['obi5_r']:+.3f} (t {v['obi5_t']:+.1f})  micro-price r {v['micro_r']:+.3f} "
                  f"(t {v['micro_t']:+.1f})  sign hit {v['obi1_sign_hit']:.0%}")
    print(f"final NLV {rep['final_nlv']}")
