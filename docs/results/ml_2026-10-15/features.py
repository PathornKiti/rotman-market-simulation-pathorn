"""Features from one recorded journal (`--record`); the price features and predictors are ritc.mlmodel (shared with the bot).

PRICE model: the mid move over the next 10 ticks of each stock, from the last 20 ticks of mids, the spread, the visible
book (top 10 levels) and traded volume. TENDER model: the path value ($/share) of a fixed tender at the moment it arrives.
Every feature uses data up to the current tick only; the target is the only thing that looks ahead.
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "evening_2026-10-14")]

sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "src"))
from ritc.core.book import OrderBook  # noqa: E402
from ritc.mlmodel import PRICE_COLS, predict, price_features  # noqa: E402,F401  (shared with the live bot)

TK = ("CRZY", "TAME", "CROC")
HORIZON = 10
TENDER_COLS = ["edge", "qty", "mom_against", "mom20_against", "exit_depth", "vol20", "spread", "dtot", "flow10", "tick",
               "dimb_our", "is_CRZY", "is_TAME", "sg"]


def heat_name(path: str) -> str:
    return os.path.basename(path)[10:25]


def _lit(x):
    return x if isinstance(x, (dict, list)) else ast.literal_eval(x)


def journal(path: str, mids_from_market: bool = False) -> dict | None:
    """Rows of one journal -> {'price': [...], 'tender': [...], 'summary': ...}; None if the market was not recorded.
    Mids = the bot's own first-loop snapshot of each tick ("tick" rows); book and volume = the recorder's FIRST row of
    the tick, the closest to that loop. `mids_from_market`: mids from those recorder rows too (what a journal replay
    feeds the bot; only for checking that the replayed bot computes the same predictions)."""
    from path_cf import cf
    rows = [json.loads(line) for line in open(path)]
    if not any(r["k"] == "market" for r in rows):
        return None
    h = heat_name(path)
    ticks = {r["tick"]: r for r in rows if r["k"] == "tick"}
    mid = {tk: {t: (r["bid"][tk] + r["ask"][tk]) / 2 for t, r in ticks.items() if r["bid"][tk] and r["ask"][tk]} for tk in TK}
    spr = {tk: {t: r["ask"][tk] - r["bid"][tk] for t, r in ticks.items() if r["bid"][tk] and r["ask"][tk]} for tk in TK}
    if mids_from_market:
        mid, spr = {tk: {} for tk in TK}, {tk: {} for tk in TK}
    books, vol, me = {tk: {} for tk in TK}, {tk: {} for tk in TK}, None
    for r in rows:
        if r["k"] != "market" or "books" not in r or "case" not in r:
            continue
        try:
            t = int(_lit(r["case"])["tick"])                 # the market's own tick (the row's "tick" can lag by 1)
            bk, secs = _lit(r["books"]), _lit(r["securities"])
            me = _lit(r["trader"]).get("trader_id", me) if r.get("trader") else me
        except (ValueError, SyntaxError):
            continue
        for tk in TK:
            if tk in bk and t not in books[tk]:
                books[tk][t] = OrderBook.from_api(bk[tk], tk, me)        # the live bot's own parser
        for s in secs:
            if s["ticker"] in TK and t not in vol[s["ticker"]]:
                vol[s["ticker"]][t] = s["volume"]
                if mids_from_market and s.get("bid") and s.get("ask"):
                    mid[s["ticker"]][t] = (s["bid"] + s["ask"]) / 2
                    spr[s["ticker"]][t] = s["ask"] - s["bid"]

    def feat(tk: str, t: int) -> dict | None:
        """The live bot's features at tick t: mids / volumes of the ticks recorded up to t (as the bot sees them)."""
        m, v = mid[tk], vol[tk]
        if t not in m:
            return None
        mids = [m[k] for k in range(t - 20, t + 1) if k in m]
        vols = [v[k] for k in range(t - 10, t + 1) if k in v]
        return price_features(mids, vols, books[tk].get(t, OrderBook(tk)), spr[tk].get(t, 0.0), t, tk)

    price = []
    for tk in TK:
        for t in range(25, 420 - HORIZON - 5):
            f = feat(tk, t)
            if f and t + HORIZON in mid[tk]:
                f.update(heat=h, tk=tk, y=mid[tk][t + HORIZON] - mid[tk][t],
                         pos=ticks[t]["pos"][tk] if t in ticks else 0)
                price.append(f)

    seen = {r["tid"]: r for r in rows if r["k"] == "tender_seen"}
    dec = {r["tid"]: r for r in rows if r["k"] == "tender_decision"}
    tender = []
    for tid, s in seen.items():
        if not s["fixed"] or s["price"] is None:
            continue
        f = feat(s["ticker"], s["tick"])
        if not f:
            continue
        sg = 1 if s["action"] == "BUY" else -1
        tender.append(dict(heat=h, tid=tid, tk=s["ticker"], sg=sg, edge=sg * (s["mid"] - s["price"]), qty=s["qty"],
                           mom_against=-sg * f["r10"], mom20_against=-sg * f["r20"],
                           exit_depth=f["bid_d"] if sg > 0 else f["ask_d"], dimb_our=sg * f["dimb"],
                           **{k: f[k] for k in ("vol20", "spread", "dtot", "flow10", "tick", "is_CRZY", "is_TAME")},
                           y=(cf(ticks, s, 1000)[1] + cf(ticks, s, 1500)[1]) / 2 / s["qty"],
                           live_accept=bool((dec.get(tid) or {}).get("accept"))))
    summary = next((r for r in rows if r["k"] == "summary"), None)
    return dict(heat=h, price=price, tender=tender, summary=summary)


