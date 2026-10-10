"""
Instrumented lock-step replay of the CURRENT `liability` bot on a simulator seed, and the ledger maths.

The replay records what a log cannot show: every fill (maker / taker, fee, the mid before and after it,
shares that opened a position, shares traded while a tender on that stock was undecided), every tender
with its hidden reserve and winner-take-all rival, the bot's answer and estimate, each booking, the
position and P&L each tick, and the close-out.

P&L decomposition (exact: the parts add up to the score)

    tender edge     sum over bookings of  q x (mid at booking - tender price)        selection + pricing
    spread (maker)  sum over resting fills of  q x (mid - fill price)                 earned / adverse fills
    spread (taker)  sum over crossing fills of  q x (mid - fill price)                paid to cross
    fees            commissions
    self-impact     each crossing fill's move of the mid x the position after it      liquidity cost
    inventory       the rest of position x mid changes (the random walk, other desks)  luck / risk
    close-out       end position x (close price - mid)
    fines           speculation / front-running

Counterfactuals flip one tender decision at a time (accept a declined tender; win a lost auction at the
hidden reserve or one cent past the rival; decline an accepted tender) and replay the heat. The change
is split into "skill" (every part except inventory) and "luck" (inventory: what the random walk happened
to do to the extra or missing position). In a hostile market the crowd's moves land in "inventory" too,
so there the total, not the skill part, is the honest number.
"""

from __future__ import annotations

import logging
import os
import random
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager

from ..sim.server import InProcessAdapter, Market

TICKERS = ("CRZY", "TAME", "CROC")
FINE1, FINE2, FINE_STEP = 0.20, 0.40, 5000          # brief: $0.20/share for the first 5,000, then $0.40
LOOPS_PER_TICK = 4
PARTS = ("edge", "spread_maker", "spread_taker", "fees", "impact", "inventory", "closeout", "fines")


def fine_schedule(shares: int) -> float:
    return FINE1 * min(shares, FINE_STEP) + FINE2 * max(0, shares - FINE_STEP)


class Instrumented(Market):
    def __init__(self, *a, **k):
        self.rec = {"fills": [], "bookings": [], "tenders": {}, "answers": {}, "ticks": [], "close": None}
        super().__init__(*a, **k)

    def _apply_fill(self, s, action, qty, px, maker):
        und = self._undecided(s.ticker)
        pen0, spec0, pos0, mid0 = self.penalty, self.state.get("spec_shares", 0), s.position, s.mid
        super()._apply_fill(s, action, qty, px, maker)
        self.rec["fills"].append({
            "tick": self.abs_tick, "ticker": s.ticker, "action": action, "qty": int(qty), "px": float(px),
            "maker": bool(maker), "fee": 0.0 if (maker and not self.stress["maker_fee"]) else s.fee * qty,
            "pos_before": pos0, "pos_after": s.position, "spec": self.state.get("spec_shares", 0) - spec0,
            "fine": self.penalty - pen0, "undecided": und, "mid": mid0, "mid_after": s.mid})

    def _book_tenders(self):
        due = [b for b in self.bookings if b[0] <= self.abs_tick]
        super()._book_tenders()
        for _, t, price in due:
            sign = 1 if t["action"] == "BUY" else -1
            self.rec["bookings"].append({"tick": self.abs_tick, "tid": t["tender_id"], "ticker": t["ticker"],
                                         "qty": sign * t["quantity"], "price": float(price),
                                         "mid": self.secs[t["ticker"]].mid})

    def _tick_liability(self):
        before = set(self.tenders)
        super()._tick_liability()
        for tid in set(self.tenders) - before:
            t = self.tenders[tid]
            s = self.secs[t["ticker"]]
            self.rec["tenders"][tid] = {
                "tid": tid, "tick": self.abs_tick, "ticker": t["ticker"], "action": t["action"],
                "qty": t["quantity"], "fixed": t["is_fixed_bid"], "price": t["price"], "expires": t["expires"],
                "reserve": t["_reserve"], "rival": t["_rival"], "caption": t["caption"],
                "kind": "private" if t["is_fixed_bid"] else ("wta" if t["caption"].startswith("Winner") else "auction"),
                "mid": s.mid, "book": self.book_view(s, 20)}

    def _close_out(self):
        pos = {t: s.position for t, s in self.secs.items()}
        mids = {t: s.mid for t, s in self.secs.items()}
        px = {}
        for t, q in pos.items():
            if q:
                side = random.Random(f"{self.fill_seed}:{t}:last").choice([-1, 1])
                s = self.secs[t]
                px[t] = mids[t] + side * s.spread / 2 - (1 if q > 0 else -1) * self.stress["close_slip"]
        super()._close_out()
        self.rec["close"] = {"pos": pos, "mid": mids, "px": px}

    def advance(self):
        super().advance()
        if self.status != "ACTIVE":
            return
        self.rec["ticks"].append({
            "tick": self.abs_tick, "mid": {t: s.mid for t, s in self.secs.items()},
            "pos": {t: s.position for t, s in self.secs.items()}, "nlv": self.nlv(), "penalty": self.penalty,
            "undecided": [t for t in self.secs if self._undecided(t)]})


class RecordingAdapter(InProcessAdapter):
    """In-process transport that also records every tender answer with the book the bot answered on."""

    def send(self, request, **kwargs):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(request.url)
        path = u.path[3:] if u.path.startswith("/v1") else u.path
        parts = path.rstrip("/").split("/")
        snap = None
        if len(parts) == 3 and parts[1] == "tenders" and request.method in ("POST", "DELETE"):
            tid = int(parts[2])
            t = self.m.tenders.get(tid)
            if t is not None:
                s = self.m.secs[t["ticker"]]
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                snap = {"tid": tid, "tick": self.m.abs_tick, "method": request.method, "price_q": q.get("price"),
                        "mid": s.mid, "book": self.m.book_view(s, 20),
                        "pos": {k: v.position for k, v in self.m.secs.items()}}
        r = super().send(request, **kwargs)
        if snap is not None:
            snap["code"] = r.status_code
            try:
                snap["resp"] = r.json()
            except ValueError:
                snap["resp"] = None
            self.m.rec["answers"][snap["tid"]] = snap
        return r


@contextmanager
def decision_hook(market: Market, force_tid: int | None = None, force_mode: str | None = None):
    """
    Record the bot's estimate for every tender and, optionally, flip ONE decision:
    "decline" an accepted tender, or "accept" a declined one (an auction at its hidden reserve, a
    winner-take-all one cent past the rival). Restores the strategy module on exit.
    """
    import ritc.strategies.liability as liab
    orig = liab.evaluate_tender
    evals: dict[int, dict] = {}

    def hooked(tender, book, *a, **k):
        d = orig(tender, book, *a, **k)
        tid = int(tender.get("tender_id", -1))
        evals[tid] = {"accept": d.accept, "price": d.price, "pps": d.profit_per_share, "vwap": d.unwind_vwap,
                      "reason": d.reason}
        if force_tid is None or tid != force_tid:
            return d
        if force_mode == "decline":
            return liab.TenderDecision(False, d.price, d.profit_per_share, d.unwind_vwap, "FORCED decline")
        price = d.price
        t = market.tenders.get(tid)
        if not tender.get("is_fixed_bid", True) and t is not None:
            buy = str(tender.get("action")).upper() == "BUY"
            price = t["_reserve"]
            if t["_rival"] is not None:
                beat = round(t["_rival"] + (0.01 if buy else -0.01), 2)
                price = max(price, beat) if buy else min(price, beat)
        return liab.TenderDecision(True, price, d.profit_per_share, d.unwind_vwap, "FORCED accept")

    liab.evaluate_tender = hooked
    try:
        yield evals
    finally:
        liab.evaluate_tender = orig


class _ListHandler(logging.Handler):
    def __init__(self, market):
        super().__init__(logging.DEBUG)
        self.m, self.lines = market, []

    def emit(self, record):
        self.lines.append(f"tick {self.m.abs_tick:>3} {record.levelname:<5} {record.name} | {record.getMessage()}")


def replay(seed: int, stress: str = "", hostile: float = 0.0, overrides: dict | None = None, dry: bool = False,
           force_tid: int | None = None, force_mode: str | None = None, keep_log: bool = True,
           config_path: str | None = None, queue: bool = False, loops_per_tick: int = LOOPS_PER_TICK) -> dict:
    from ..cli import build
    old = os.environ.get("RITC_STRESS"), os.environ.get("RIT_URL")
    os.environ["RITC_STRESS"] = stress
    os.environ["RIT_URL"] = "http://127.0.0.1:9/v1"           # never used: the adapter answers in-process
    root = logging.getLogger()
    saved = root.handlers[:], root.level, logging.root.manager.disable
    try:
        m = Instrumented("liability", 420, 1, seed, hostile, queue)
        m.status = "ACTIVE"
        h = _ListHandler(m)
        root.handlers = [h] if keep_log else [logging.NullHandler()]
        root.setLevel(logging.INFO)
        logging.disable(logging.NOTSET)
        with decision_hook(m, force_tid, force_mode) as evals:
            runner, cfg = build("liability", config_path, live=not dry, overrides=overrides or {})
            if dry:
                runner.s.ex.dry_run = True
            runner.client.adapter = RecordingAdapter(m)
            runner.interval = 0.0
            runner.on_loop = lambda: m.advance() if runner.loops % loops_per_tick == 0 else None
            runner.run()
    finally:
        root.handlers, lvl, dis = saved[0], saved[1], saved[2]
        root.setLevel(lvl)
        logging.disable(dis)
        for k, v in zip(("RITC_STRESS", "RIT_URL"), old):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"seed": seed, "stress": stress, "hostile": hostile, "overrides": overrides or {}, "dry": dry,
            "score": m.score(), "nlv": m.nlv(), "penalty": m.penalty, "spec_shares": m.state.get("spec_shares", 0),
            "limit_rejects": m.state.get("limit_rejects", 0), "rec": m.rec, "evals": evals,
            "log": h.lines if keep_log else [], "cfg": cfg}


# ---------------------------------------------------------------------------------------------- ledger maths
def decompose(r: dict) -> dict:
    rec = r["rec"]
    close = rec.get("close") or {"pos": {}, "mid": {t: r["rec"]["ticks"][-1]["mid"][t] for t in TICKERS}, "px": {}}
    mid_end = close["mid"]
    by = {t: defaultdict(float) for t in TICKERS}
    for b in rec["bookings"]:
        c = by[b["ticker"]]
        c["edge"] += b["qty"] * (b["mid"] - b["price"])
        c["mtm"] += b["qty"] * (mid_end[b["ticker"]] - b["mid"])
    for f in rec["fills"]:
        q = f["qty"] if f["action"] == "BUY" else -f["qty"]
        c = by[f["ticker"]]
        c["spread_maker" if f["maker"] else "spread_taker"] += q * (f["mid"] - f["px"])
        c["fees"] -= f["fee"]
        c["impact"] += (f["mid_after"] - f["mid"]) * f["pos_after"]
        c["mtm"] += q * (mid_end[f["ticker"]] - f["mid"])
    for t in TICKERS:
        q = close["pos"].get(t, 0)
        if q:
            by[t]["closeout"] += q * (close["px"].get(t, mid_end[t]) - mid_end[t])
        by[t]["inventory"] = by[t].pop("mtm", 0.0) - by[t]["impact"]
    total = defaultdict(float)
    for t in TICKERS:
        for k, v in by[t].items():
            total[k] += v
    total["fines"] = -r["penalty"]
    total = {k: total.get(k, 0.0) for k in PARTS}
    return {"total": total, "by_ticker": {t: {k: by[t].get(k, 0.0) for k in PARTS if k != "fines"} for t in TICKERS},
            "check": sum(total.values()) - r["score"]}


def attribution(r: dict) -> dict:
    """FIFO lots per stock: realized P&L of each tender, however its shares left (market, later tender, close)."""
    rec = r["rec"]
    ev = [(b["tick"], 0.0, b["ticker"], b["qty"], b["price"], 0.0, "tender", b["tid"]) for b in rec["bookings"]]
    for i, f in enumerate(rec["fills"]):
        q = f["qty"] if f["action"] == "BUY" else -f["qty"]
        ev.append((f["tick"], 1 + i * 1e-6, f["ticker"], q, f["px"], f["fee"] / f["qty"],
                   "maker" if f["maker"] else "taker", None))
    ev.sort(key=lambda e: (e[0], e[1]))
    lots = {t: deque() for t in TICKERS}
    out = defaultdict(lambda: {"pnl": 0.0, "fees": 0.0, "via_maker": 0, "via_taker": 0, "via_tender": 0,
                               "via_close": 0, "last_exit": None, "opened": 0})
    for tick, _, t, q, px, fee_ps, kind, tid in ev:
        while q and lots[t] and (lots[t][0][1] > 0) != (q > 0):
            lot = lots[t][0]
            take = min(abs(q), abs(lot[1]))
            sgn = 1 if lot[1] > 0 else -1
            o = out[lot[0]]
            o["pnl"] += take * (px - lot[2]) * sgn - take * fee_ps
            o["fees"] += take * fee_ps
            o["via_" + kind] += take
            o["last_exit"] = tick
            lot[1] -= sgn * take
            q += take if q < 0 else -take
            if lot[1] == 0:
                lots[t].popleft()
        if q:
            key = tid if kind == "tender" else f"spec:{t}"
            if kind != "tender":
                out[key]["opened"] += abs(q)
                out[key]["pnl"] -= abs(q) * fee_ps
            lots[t].append([key, q, px])
    close = rec.get("close") or {"px": {}, "mid": {}}
    for t in TICKERS:
        cpx = close["px"].get(t, close["mid"].get(t))
        for lot in lots[t]:
            if cpx is None:
                continue
            o = out[lot[0]]
            o["pnl"] += abs(lot[1]) * (cpx - lot[2]) * (1 if lot[1] > 0 else -1)
            o["via_close"] += abs(lot[1])
            o["last_exit"] = 421
    return dict(out)


def tender_table(r: dict) -> list[dict]:
    rec, evals = r["rec"], r["evals"]
    booked = {b["tid"]: b for b in rec["bookings"]}
    lots = attribution(r)
    rows = []
    for tid, t in sorted(rec["tenders"].items()):
        sign = 1 if t["action"] == "BUY" else -1
        a, e, b, lot = rec["answers"].get(tid), evals.get(tid, {}), booked.get(tid), lots.get(tid)
        row = {k: v for k, v in t.items() if k != "book"}
        row.update({
            "edge_arrival": sign * (t["mid"] - t["price"]) if t["fixed"] else None,
            "answer_tick": a["tick"] if a else None,
            "ticks_before_expiry": (t["expires"] - a["tick"]) if a else None,
            "answer": None if not a else ("decline" if a["method"] == "DELETE" else "accept"),
            "bid": float(a["price_q"]) if a and a.get("price_q") not in (None, "") else None,
            "resp_success": (a.get("resp") or {}).get("success") if a and isinstance(a.get("resp"), dict) else None,
            "resp_code": a.get("code") if a else None,
            "mid_answer": a["mid"] if a else None,
            "edge_answer": sign * (a["mid"] - t["price"]) if (a and t["fixed"]) else None,
            "pps_est": e.get("pps"), "reason": e.get("reason"), "vwap_est": e.get("vwap"),
            "booked_tick": b["tick"] if b else None, "booked_price": b["price"] if b else None,
            "book_answer": a["book"] if a else None,
        })
        if b and lot:
            row.update({"pnl": lot["pnl"], "pnl_ps": lot["pnl"] / t["qty"], "via_maker": lot["via_maker"],
                        "via_taker": lot["via_taker"], "via_tender": lot["via_tender"], "via_close": lot["via_close"],
                        "exit_ticks": (lot["last_exit"] - b["tick"]) if lot["last_exit"] else None})
        if not t["fixed"] and row["bid"] is not None:
            row["bid_vs_reserve"] = sign * (row["bid"] - t["reserve"])     # >= 0: past the reserve
            row["bid_vs_rival"] = sign * (row["bid"] - t["rival"]) if t["rival"] is not None else None
        rows.append(row)
    return rows


def execution(r: dict) -> dict:
    out = {}
    for t in TICKERS:
        fs = [f for f in r["rec"]["fills"] if f["ticker"] == t]
        q = lambda f: f["qty"] if f["action"] == "BUY" else -f["qty"]   # noqa: E731
        out[t] = {"fills": len(fs),
                  "maker_sh": sum(f["qty"] for f in fs if f["maker"]),
                  "taker_sh": sum(f["qty"] for f in fs if not f["maker"]),
                  "maker_capture": sum(q(f) * (f["mid"] - f["px"]) for f in fs if f["maker"]),
                  "taker_cost": sum(q(f) * (f["mid"] - f["px"]) for f in fs if not f["maker"]),
                  "adverse_sh": sum(f["qty"] for f in fs if f["maker"] and q(f) * (f["mid"] - f["px"]) < 0),
                  "fees": sum(f["fee"] for f in fs), "spec_sh": sum(f["spec"] for f in fs),
                  "undecided_sh": sum(f["qty"] for f in fs if f["undecided"])}
    return out


def inventory(r: dict, sigma: dict[str, float] | None = None) -> dict:
    ticks = r["rec"]["ticks"]
    sigma = sigma or {"CRZY": 0.02, "TAME": 0.025, "CROC": 0.04}
    gross = [sum(abs(q) for q in x["pos"].values()) for x in ticks]
    net = [sum(x["pos"].values()) for x in ticks]
    risk = [sum(abs(q) * sigma.get(tk, 0.03) * max(420 - x["tick"], 0) ** 0.5 for tk, q in x["pos"].items())
            for x in ticks]
    close = r["rec"].get("close") or {"pos": {}}
    return {"peak_gross": max(gross, default=0), "peak_net": max((abs(n) for n in net), default=0),
            "avg_gross": sum(gross) / len(gross) if gross else 0.0, "peak_risk": max(risk, default=0.0),
            "avg_risk": sum(risk) / len(risk) if risk else 0.0, "end_pos": close.get("pos", {}),
            "end_gross": sum(abs(q) for q in close.get("pos", {}).values()),
            "series": [{"tick": x["tick"], "pos": x["pos"], "nlv": x["nlv"], "mid": x["mid"]} for x in ticks]}


def fines(r: dict) -> dict:
    """Fines under the lenient reading (only shares that open a position) and the strict one (any trade
    in a stock while one of its tenders - or our auction bid on it - is still undecided)."""
    spec = sum(f["spec"] for f in r["rec"]["fills"])
    und = sum(f["qty"] for f in r["rec"]["fills"] if f["undecided"] and not f["spec"])
    return {"spec_shares": spec, "lenient": fine_schedule(spec), "undecided_shares": und,
            "strict": fine_schedule(spec + und), "charged": r["penalty"]}


def _flip(args):
    seed, stress, hostile, overrides, tid, mode = args
    r = replay(seed, stress, hostile, overrides, force_tid=tid, force_mode=mode, keep_log=False)
    return tid, mode, r["score"], decompose(r)["total"]


def counterfactuals(base: dict, workers: int | None = None) -> list[dict]:
    rows = tender_table(base)
    jobs = [(base["seed"], base["stress"], base["hostile"], base["overrides"], row["tid"],
             "decline" if row["booked_tick"] is not None else "accept")
            for row in rows if row["answer"] is not None]
    b = decompose(base)["total"]
    out = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for tid, mode, score, d in pool.map(_flip, jobs):
            luck = d["inventory"] - b["inventory"]
            out.append({"tid": tid, "mode": mode, "score": score, "delta": score - base["score"],
                        "luck": luck, "skill": score - base["score"] - luck,
                        "parts": {k: d[k] - b[k] for k in PARTS}})
    return out


def flip_category(row: dict) -> str:
    """Bucket of a tender for the gap table (by kind, outcome and edge at the moment we answered)."""
    if row["kind"] != "private":
        if row["booked_tick"] is not None:
            return f"{row['kind']} won"
        if row.get("bid_vs_reserve") is not None and row["bid_vs_reserve"] < 0:
            return f"{row['kind']} lost: bid short of reserve"
        return f"{row['kind']} lost: rival bid better"
    if row["booked_tick"] is not None:
        return "private accepted"
    if row["reason"] and "risk room" in row["reason"]:
        return "private declined: no limit room"
    e = row["edge_answer"]
    if e is None:
        return "private declined"
    if e > 0.10:
        return "private declined: edge > 10c"
    if e > 0:
        return "private declined: edge 0-10c"
    return "private declined: edge <= 0"


def window_fills(r: dict) -> list[dict]:
    """Our fills while a tender on that stock was undecided, and whether the window had only just opened (the
    tender arrived in that same tick, before the bot could see it and pull its resting orders)."""
    rec = r["rec"]
    out = []
    for f in rec["fills"]:
        if not f["undecided"]:
            continue
        new = sorted(t["tid"] for t in rec["tenders"].values() if t["ticker"] == f["ticker"] and t["tick"] == f["tick"])
        out.append({"tick": f["tick"], "ticker": f["ticker"], "action": f["action"], "qty": f["qty"],
                    "maker": f["maker"], "pos_before": f["pos_before"], "pos_after": f["pos_after"],
                    "reduces": abs(f["pos_after"]) < abs(f["pos_before"]), "arrival_race": bool(new), "tenders": new})
    return out
