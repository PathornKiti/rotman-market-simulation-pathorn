"""
Offline stand-in for the RIT Client REST API - one simulator, all five cases.

The real RIT client is Windows-only and you get no practice run on the day.
This serves the same endpoints on localhost:9999 with a simulated market so you
can prove a bot works end-to-end, tune thresholds and read the logs first:

    python -m ritc sim liability            # terminal 1
    python -m ritc run liability --live     # terminal 2 (live = against the SIM)

It is a SIMULATOR, not an emulator. Fills are approximate and the "other
traders" are noise. Use it to debug logic and plumbing, not to predict a score.
The tickers it creates match the default files in config/, so both work out of
the box.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..pricing.options import bs_price

TRADER_ID = "SIMBOT"
TICK = 0.01


@dataclass
class Sec:
    ticker: str
    mid: float
    spread: float = 0.04
    lot: int = 1000
    sigma: float = 0.03          # per-tick std of the mid
    fee: float = 0.02            # taker fee per unit
    rebate: float = 0.0          # maker rebate per unit
    mult: int = 1
    max_trade: int = 10_000
    impact: float = 0.0          # mid move per unit we take
    kind: str = "STOCK"
    position: int = 0
    cost: float = 0.0            # signed cost basis for vwap
    volume: int = 0
    last: float = 0.0
    bids: list[list[float]] = field(default_factory=list)   # [price, qty]
    asks: list[list[float]] = field(default_factory=list)
    tradeable: bool = True
    hist: list[dict] = field(default_factory=list)

    def rebuild(self, rng: random.Random, levels: int = 10) -> None:
        half = max(self.spread / 2, TICK)
        bb = math.floor((self.mid - half) / TICK + 1e-9) * TICK
        ba = math.ceil((self.mid + half) / TICK - 1e-9) * TICK
        if ba <= bb:
            ba = bb + TICK
        if not self.tradeable:
            self.bids, self.asks = [], []
            return
        bb, ba = max(bb, TICK), max(ba, 2 * TICK)
        self.bids = [[round(bb - i * TICK * (1 + i // 3), 2), self.lot * rng.randint(1, 4)] for i in range(levels)]
        self.bids = [lv for lv in self.bids if lv[0] >= TICK]
        self.asks = [[round(ba + i * TICK * (1 + i // 3), 2), self.lot * rng.randint(1, 4)] for i in range(levels)]


class Market:
    def __init__(self, case: str, ticks_per_period: int, periods: int, seed: int | None):
        # `rng` drives the MARKET (prices, news, tenders, book sizes) and nothing else.
        # Passive fills never draw from it. When they did, every resting
        # order the bot posts consumes draws and shifts the whole future price path, so the
        # "same seed" was a different market on every run - A/B tests of a market maker
        # were pure noise. Now a seed is the same market for every setting being compared.
        self.rng = random.Random(seed)
        # Passive fills use COMMON RANDOM NUMBERS: the passive flow hitting each side of each
        # book on each tick is drawn from a generator keyed on (seed, tick, ticker, side), not
        # from one sequential stream. With a sequential stream, a setting that rests one more
        # order at the touch shifted every later draw, so two settings got unrelated fill luck
        # and the A/B difference was mostly noise (paired SE ~2x larger, docs/PERFORMANCE.md).
        self.fill_seed = seed if seed is not None else random.getrandbits(32)
        self.case = case
        self.tpp = ticks_per_period
        self.periods = periods
        self.period, self.tick = 1, 0
        self.status = "PAUSED"
        self.cash = 0.0
        self.secs: dict[str, Sec] = {}
        self.orders: dict[int, dict] = {}
        self.done_orders: list[dict] = []
        self.news: list[dict] = []
        self.tenders: dict[int, dict] = {}
        self.limits: list[dict] = []
        self.ids = itertools.count(1)
        self.lock = threading.RLock()
        self.state: dict = {}
        getattr(self, f"_setup_{case}")()
        for s in self.secs.values():
            s.last = s.mid
            s.rebuild(self.rng)

    # ===================================================== case universes
    def _setup_liability(self) -> None:
        for t, px in (("CRZY", 25.0), ("TAME", 40.0)):
            self.secs[t] = Sec(t, px, spread=0.04, lot=2000, sigma=0.025, fee=0.02, impact=2e-6, max_trade=10_000)
        self.limits = [{"name": "equity", "gross": 0, "net": 0, "gross_limit": 250_000, "net_limit": 150_000}]

    def _setup_etf(self) -> None:
        self.secs["BULL"] = Sec("BULL", 20.0, sigma=0.03, fee=0.02, lot=2000)
        self.secs["BEAR"] = Sec("BEAR", 15.0, sigma=0.03, fee=0.02, lot=2000)
        self.secs["RITC"] = Sec("RITC", 35.0, sigma=0.0, fee=0.03, lot=1500, spread=0.05)
        self.state["premium"] = 0.0
        self.limits = [{"name": "equity", "gross": 0, "net": 0, "gross_limit": 300_000, "net_limit": 200_000}]

    def _setup_equity(self) -> None:
        for t, px, sg in (("SPNG", 15.0, 0.02), ("SMMR", 25.0, 0.03), ("ATMN", 30.0, 0.04)):
            self.secs[t] = Sec(t, px, spread=0.06, sigma=sg, fee=0.02, rebate=0.01, lot=1000)
        self.limits = [{"name": "equity", "gross": 0, "net": 0, "gross_limit": 200_000, "net_limit": 100_000}]

    def _setup_derivatives(self) -> None:
        self.state.update(true_vol=0.20, mkt_vol=0.20, week=-1)
        self.secs["RTM"] = Sec("RTM", 50.0, spread=0.02, lot=5000, fee=0.01, max_trade=10_000)
        for exp, exp_tick in (("1", 300), ("2", 600)):
            for k in (46, 48, 50, 52, 54):
                for cp in "CP":
                    t = f"RTM{exp}{cp}{k}"
                    self.secs[t] = Sec(t, 1.0, spread=0.04, lot=50, fee=0.01, mult=100, kind="OPTION",
                                       max_trade=100)
                    self.state[t] = (exp_tick, cp == "C", float(k))
        self.limits = [{"name": "options", "gross": 0, "net": 0, "gross_limit": 2500, "net_limit": 1000}]
        self._price_options()

    def _setup_commodity(self) -> None:
        self.state.update(carry=0.002, basis_noise={}, pending=[])
        self.secs["CL"] = Sec("CL", 70.0, spread=0.04, lot=50, sigma=0.04, fee=0.02, max_trade=50)
        for t, exp in (("CL-1F", 300), ("CL-2F", 600)):
            self.secs[t] = Sec(t, 70.0, spread=0.03, lot=50, sigma=0.0, fee=0.02, max_trade=50, kind="FUTURE")
            self.state[t] = exp
            self.state["basis_noise"][t] = 0.0
        self.limits = [{"name": "crude", "gross": 0, "net": 0, "gross_limit": 500, "net_limit": 200}]
        self._price_futures()

    # ===================================================== per-tick dynamics
    @property
    def abs_tick(self) -> int:
        return (self.period - 1) * self.tpp + self.tick

    def advance(self) -> None:
        with self.lock:
            if self.status != "ACTIVE":
                return
            self.tick += 1
            if self.tick > self.tpp:
                if self.period >= self.periods:
                    self.status = "STOPPED"
                    self._close_out()
                    return
                self.period, self.tick = self.period + 1, 1
            for s in self.secs.values():
                if s.sigma:
                    s.mid = max(0.5, s.mid + self.rng.gauss(0, s.sigma))
            getattr(self, f"_tick_{self.case}")()
            for s in self.secs.values():
                s.last = s.mid
                s.rebuild(self.rng)
                s.hist.append({"period": self.period, "tick": self.tick, "open": s.mid, "high": s.mid,
                               "low": s.mid, "close": round(s.mid, 4)})
            self._fill_resting()

    def _tick_liability(self) -> None:
        for tid in [k for k, v in self.tenders.items() if v["expires"] <= self.abs_tick]:
            self.tenders.pop(tid)
        if self.abs_tick % 12 == 5:
            t = self.rng.choice(list(self.secs))
            s = self.secs[t]
            action = self.rng.choice(["BUY", "SELL"])
            qty = self.rng.choice([10_000, 20_000, 30_000, 50_000])
            edge = self.rng.uniform(-0.10, 0.25)
            price = round(s.mid - edge if action == "BUY" else s.mid + edge, 2)
            fixed = self.rng.random() > 0.25
            tid = next(self.ids)
            self.tenders[tid] = {
                "tender_id": tid, "period": self.period, "tick": self.tick, "expires": self.abs_tick + 10,
                "caption": f"Client wants you to {action} {qty} {t}", "quantity": qty, "action": action,
                "is_fixed_bid": fixed, "price": price if fixed else None, "ticker": t,
                "_reserve": round(s.mid - 0.05 if action == "BUY" else s.mid + 0.05, 2),
            }

    def _tick_etf(self) -> None:
        p = 0.92 * self.state["premium"] + self.rng.gauss(0, 0.06)
        self.state["premium"] = p
        self.secs["RITC"].mid = self.secs["BULL"].mid + self.secs["BEAR"].mid + p

    def _tick_equity(self) -> None:
        if self.rng.random() < 0.02:                   # occasional informed jump
            s = self.rng.choice(list(self.secs.values()))
            s.mid += self.rng.choice([-1, 1]) * self.rng.uniform(0.10, 0.30)

    def _tick_derivatives(self) -> None:
        week = (self.abs_tick - 1) // 75
        if week != self.state["week"]:
            self.state["week"] = week
            if week == 0:
                self._news("Delta limit", "The delta limit for this heat is 7,000 shares. Penalty $0.01/share/tick.")
            prev = self.state["true_vol"]
            self.state["true_vol"] = self.rng.choice([0.15, 0.20, 0.25, 0.30, 0.35])
            # The market only half-believes the news: implied vol lags true vol.
            self.state["mkt_vol"] = 0.5 * prev + 0.5 * self.state["true_vol"]
            self._news(f"Volatility week {week + 1}",
                       f"The realized volatility of RTM for this week will be {round(self.state['true_vol'] * 100)}%")
        if (self.abs_tick - 1) % 75 == 37:
            v = self.state["true_vol"]
            self._news("Volatility forecast", f"The annualized volatility of RTM next week will be between "
                       f"{round(v * 100) - 3}% and {round(v * 100) + 3}%")
        self.state["mkt_vol"] += 0.05 * (self.state["true_vol"] - self.state["mkt_vol"]) + self.rng.gauss(0, 0.002)
        rtm = self.secs["RTM"]
        rtm.mid = max(1.0, rtm.mid * math.exp(self.rng.gauss(0, self.state["true_vol"] * math.sqrt(1 / 3600))))
        self._price_options()

    def _price_options(self) -> None:
        S = self.secs["RTM"].mid
        for t, s in self.secs.items():
            if s.kind != "OPTION":
                continue
            exp, is_call, K = self.state[t]
            T = (exp - self.abs_tick) / 3600
            if T <= 0:
                if s.tradeable:            # settle at intrinsic
                    intrinsic = max(S - K, 0) if is_call else max(K - S, 0)
                    self.cash += s.position * s.mult * intrinsic
                    s.position, s.cost, s.tradeable, s.mid = 0, 0.0, False, intrinsic
                continue
            s.mid = max(0.01, bs_price(S, K, T, 0.0, self.state["mkt_vol"], is_call))

    def _tick_commodity(self) -> None:
        for p in [p for p in self.state["pending"] if p[0] > 0]:
            self.secs["CL"].mid += p[1]
            p[0] -= 1
        self.state["pending"] = [p for p in self.state["pending"] if p[0] > 0]
        if self.abs_tick % 40 == 20:
            exp = round(self.rng.uniform(-2, 2), 1)
            act = round(exp + self.rng.gauss(0, 2), 1)
            word = lambda x: "build" if x >= 0 else "draw"   # noqa: E731
            self._news("EIA inventory report",
                       f"Crude inventories show a {word(act)} of {abs(act)} million barrels vs expected "
                       f"{word(exp)} of {abs(exp)} million barrels")
            # Price reacts over 5 ticks: -0.25 $/mm bbl of surprise.
            self.state["pending"].append([5, -0.25 * (act - exp) / 5])
        self._price_futures()

    def _price_futures(self) -> None:
        S = self.secs["CL"].mid
        for t in ("CL-1F", "CL-2F"):
            s = self.secs[t]
            ttx = self.state[t] - self.abs_tick
            if ttx <= 0:
                if s.tradeable:
                    self.cash += s.position * s.mult * S     # cash-settle at spot
                    s.position, s.cost, s.tradeable = 0, 0.0, False
                    s.mid = S
                continue
            n = 0.9 * self.state["basis_noise"][t] + self.rng.gauss(0, 0.08)
            self.state["basis_noise"][t] = n
            s.mid = S + self.state["carry"] * ttx + n

    def _news(self, headline: str, body: str) -> None:
        self.news.append({"news_id": len(self.news) + 1, "period": self.period, "tick": self.tick,
                          "ticker": "", "headline": headline, "body": body})

    # ===================================================== matching
    def _apply_fill(self, s: Sec, action: str, qty: int, px: float, maker: bool) -> None:
        sign = 1 if action == "BUY" else -1
        s.position += sign * qty
        s.cost += sign * qty * px
        s.volume += qty
        self.cash -= sign * qty * px * s.mult
        self.cash += (s.rebate if maker else -s.fee) * qty
        if not maker and s.impact:
            s.mid += sign * s.impact * qty
        if s.position == 0:
            s.cost = 0.0

    def submit(self, ticker: str, otype: str, qty: int, action: str, price: float | None) -> dict:
        s = self.secs.get(ticker)
        if s is None or not s.tradeable:
            raise ValueError(f"unknown or untradeable ticker {ticker}")
        if qty <= 0 or qty > s.max_trade:
            raise ValueError(f"quantity must be 1..{s.max_trade}")
        oid = next(self.ids)
        book = s.asks if action == "BUY" else s.bids
        left, filled_cost = qty, 0.0
        for lv in book:
            if left <= 0:
                break
            if otype == "LIMIT" and price is not None:
                if (action == "BUY" and lv[0] > price) or (action == "SELL" and lv[0] < price):
                    break
            take = int(min(left, lv[1]))
            if take <= 0:
                continue
            lv[1] -= take
            left -= take
            filled_cost += take * lv[0]
            self._apply_fill(s, action, take, lv[0], maker=False)
        filled = qty - left
        order = {"order_id": oid, "period": self.period, "tick": self.tick, "trader_id": TRADER_ID,
                 "ticker": ticker, "type": otype, "quantity": qty, "action": action,
                 "price": price, "quantity_filled": filled,
                 "vwap": filled_cost / filled if filled else None, "status": "OPEN"}
        if otype == "LIMIT" and left > 0:
            self.orders[oid] = order
        else:
            order["status"] = "TRANSACTED"
            self.done_orders.append(order)
        return order

    def _flow(self, ticker: str, action: str) -> int:
        """Shares of passive flow arriving at the touch on our side this tick (same for every setting)."""
        r = random.Random(f"{self.fill_seed}:{self.abs_tick}:{ticker}:{action}")
        return self.secs[ticker].lot * r.randint(1, 2) if r.random() < 0.35 else 0

    def _fill_resting(self) -> None:
        flow: dict[tuple[str, str], int] = {}
        # Best-priced orders first, then time priority, so they share one tick's flow like a queue.
        ranked = sorted(self.orders.items(), key=lambda kv: (-kv[1]["price"] if kv[1]["action"] == "BUY"
                                                             else kv[1]["price"], kv[0]))
        for oid, o in ranked:
            s = self.secs[o["ticker"]]
            if not s.tradeable:
                self.orders.pop(oid)
                continue
            left = o["quantity"] - o["quantity_filled"]
            p = o["price"]
            if not s.bids or not s.asks:
                continue
            best_opp = s.asks[0][0] if o["action"] == "BUY" else s.bids[0][0]
            crossed = p >= best_opp if o["action"] == "BUY" else p <= best_opp
            at_touch = p >= s.bids[0][0] if o["action"] == "BUY" else p <= s.asks[0][0]
            take = 0
            if crossed:
                take = left
            elif at_touch:
                key = (o["ticker"], o["action"])
                if key not in flow:
                    flow[key] = self._flow(*key)
                take = min(left, flow[key])
                flow[key] -= take
            if take:
                prev = o["quantity_filled"]
                o["vwap"] = ((o.get("vwap") or 0.0) * prev + p * take) / (prev + take)
                o["quantity_filled"] += take
                self._apply_fill(s, o["action"], take, p, maker=True)
                if o["quantity_filled"] >= o["quantity"]:
                    o["status"] = "TRANSACTED"
                    self.done_orders.append(self.orders.pop(oid))

    def cancel(self, oid: int) -> bool:
        o = self.orders.pop(oid, None)
        if o:
            o["status"] = "CANCELLED"
            self.done_orders.append(o)
        return o is not None

    def _close_out(self) -> None:
        for s in self.secs.values():
            if s.position and s.tradeable:
                self.cash += s.position * s.mid * s.mult
                s.position = 0

    # ===================================================== views
    def nlv(self) -> float:
        return self.cash + sum(s.position * s.mid * s.mult for s in self.secs.values() if s.tradeable)

    def sec_view(self, s: Sec) -> dict:
        bid, ask = (s.bids[0][0] if s.bids else None), (s.asks[0][0] if s.asks else None)
        return {"ticker": s.ticker, "type": s.kind, "position": s.position,
                "vwap": abs(s.cost / s.position) if s.position else 0.0,
                "last": round(s.last, 4), "bid": bid, "ask": ask,
                "bid_size": s.bids[0][1] if s.bids else 0, "ask_size": s.asks[0][1] if s.asks else 0,
                "volume": s.volume, "trading_fee": s.fee, "limit_order_rebate": s.rebate,
                "max_trade_size": s.max_trade, "multiplier": s.mult, "is_tradeable": s.tradeable,
                "unrealized": round((s.mid * s.position - s.cost) * s.mult, 2)}

    def book_view(self, s: Sec, limit: int) -> dict:
        def rows(levels, action):
            return [{"order_id": 0, "trader_id": "ANON", "ticker": s.ticker, "price": p, "quantity": q,
                     "quantity_filled": 0, "action": action, "status": "OPEN"} for p, q in levels[:limit] if q > 0]
        mine_b = [o for o in self.orders.values() if o["ticker"] == s.ticker and o["action"] == "BUY"]
        mine_a = [o for o in self.orders.values() if o["ticker"] == s.ticker and o["action"] == "SELL"]
        bids = sorted(rows(s.bids, "BUY") + mine_b, key=lambda r: -r["price"])
        asks = sorted(rows(s.asks, "SELL") + mine_a, key=lambda r: r["price"])
        return {"bid": bids[:limit], "ask": asks[:limit]}

    def limits_view(self) -> list[dict]:
        out = []
        for lim in self.limits:
            g = sum(abs(s.position) for s in self.secs.values() if s.tradeable)
            n = sum(s.position for s in self.secs.values() if s.tradeable)
            out.append({**lim, "gross": g, "net": n})
        return out


# ========================================================= HTTP layer
def make_handler(m: Market):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):        # keep the console quiet
            pass

        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _parse(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            path = u.path[3:] if u.path.startswith("/v1") else u.path
            return path.rstrip("/"), q

        def do_GET(self):
            path, q = self._parse()
            with m.lock:
                if path == "/case":
                    return self._send(200, {"name": f"SIM-{m.case.upper()}", "period": m.period, "tick": m.tick,
                                            "ticks_per_period": m.tpp, "total_periods": m.periods, "status": m.status})
                if path == "/trader":
                    return self._send(200, {"trader_id": TRADER_ID, "first_name": "Sim", "last_name": "Bot",
                                            "nlv": round(m.nlv(), 2)})
                if path == "/limits":
                    return self._send(200, m.limits_view())
                if path == "/securities":
                    t = q.get("ticker")
                    return self._send(200, [m.sec_view(s) for s in m.secs.values() if not t or s.ticker == t])
                if path == "/securities/book":
                    s = m.secs.get(q.get("ticker", ""))
                    return self._send(200 if s else 404, m.book_view(s, int(q.get("limit", 20))) if s else {})
                if path == "/securities/history":
                    s = m.secs.get(q.get("ticker", ""))
                    rows = s.hist[::-1] if s else []
                    return self._send(200, rows[: int(q["limit"])] if q.get("limit") else rows)
                if path == "/securities/tas":
                    return self._send(200, [])
                if path == "/news":
                    since = int(q.get("since", 0) or 0)
                    items = [n for n in m.news if n["news_id"] > since][::-1]
                    return self._send(200, items[: int(q.get("limit", 50))])
                if path == "/orders":
                    st = q.get("status", "OPEN")
                    rows = list(m.orders.values()) if st == "OPEN" else [o for o in m.done_orders if o["status"] == st]
                    return self._send(200, rows)
                if path == "/tenders":
                    return self._send(200, [{k: v for k, v in t.items() if not k.startswith("_")}
                                            for t in m.tenders.values()])
                if path == "/leases":
                    return self._send(200, [])
            self._send(404, {"code": "NOT_FOUND", "message": path})

        def do_POST(self):
            path, q = self._parse()
            with m.lock:
                if m.status != "ACTIVE" and (path == "/orders" or path.startswith("/tenders/")):
                    return self._send(400, {"code": "CASE_NOT_ACTIVE"})
                if path == "/orders":
                    try:
                        price = float(q["price"]) if q.get("price") else None
                        o = m.submit(q["ticker"], q.get("type", "MARKET").upper(), int(float(q["quantity"])),
                                     q["action"].upper(), price)
                        return self._send(200, o)
                    except (KeyError, ValueError) as exc:
                        return self._send(422, {"code": "BAD_ORDER", "message": str(exc)})
                if path == "/commands/cancel":
                    t = q.get("ticker")
                    ids = [oid for oid, o in m.orders.items() if q.get("all") or not t or o["ticker"] == t]
                    for oid in ids:
                        m.cancel(oid)
                    return self._send(200, {"cancelled_order_ids": ids})
                if path.startswith("/tenders/"):
                    tid = int(path.rsplit("/", 1)[1])
                    t = m.tenders.pop(tid, None)
                    if t is None:
                        return self._send(404, {"code": "TENDER_NOT_FOUND"})
                    price = t["price"]
                    if not t["is_fixed_bid"]:
                        price = float(q.get("price", 0))
                        good = price <= t["_reserve"] if t["action"] == "BUY" else price >= t["_reserve"]
                        if not good:
                            return self._send(200, {"success": False})
                    s = m.secs[t["ticker"]]
                    sign = 1 if t["action"] == "BUY" else -1
                    s.position += sign * t["quantity"]
                    s.cost += sign * t["quantity"] * price
                    m.cash -= sign * t["quantity"] * price
                    return self._send(200, {"success": True})
            self._send(404, {"code": "NOT_FOUND"})

        def do_DELETE(self):
            path, _ = self._parse()
            with m.lock:
                if path.startswith("/orders/"):
                    ok = m.cancel(int(path.rsplit("/", 1)[1]))
                    return self._send(200 if ok else 404, {"success": ok})
                if path.startswith("/tenders/"):
                    m.tenders.pop(int(path.rsplit("/", 1)[1]), None)
                    return self._send(200, {"success": True})
            self._send(404, {"code": "NOT_FOUND"})

    return H


CASE_SHAPE = {          # ticks_per_period, periods
    "liability": (300, 1), "etf": (300, 1), "equity": (300, 1),
    "derivatives": (300, 2), "commodity": (300, 2),
}


def serve(case: str, port: int = 9999, speed: float = 4.0, delay: float = 2.0, seed: int | None = None,
          block: bool = True) -> tuple[ThreadingHTTPServer, Market]:
    tpp, periods = CASE_SHAPE[case]
    m = Market(case, tpp, periods, seed)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(m))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if speed <= 0:
        # LOCK-STEP: no clock thread. The caller advances the market itself with
        # `m.advance()` (the tuner does it every N bot loops), so a run depends only on
        # the seed and the settings - not on thread timing.
        if block:
            raise ValueError("lock-step mode (speed <= 0) is for programmatic use; pass block=False")
        m.status = "ACTIVE"
        return srv, m

    def clock():
        time.sleep(delay)
        m.status = "ACTIVE"
        while m.status == "ACTIVE":
            time.sleep(1.0 / speed)
            m.advance()
            if m.tick % 25 == 0 and block:
                print(f"[sim] period {m.period} tick {m.tick:3d}  NLV {m.nlv():12,.2f}  "
                      f"pos {{{', '.join(f'{t}:{s.position}' for t, s in m.secs.items() if s.position)}}}")
        print(f"[sim] case finished. Final NLV {m.nlv():,.2f}")

    th = threading.Thread(target=clock, daemon=True)
    th.start()
    if block:
        print(f"[sim] {case} on http://127.0.0.1:{port}/v1  ({tpp} ticks x {periods} periods, {speed} ticks/s)")
        try:
            while th.is_alive():
                th.join(0.5)
        except KeyboardInterrupt:
            pass
        srv.shutdown()
    return srv, m


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Offline RIT simulator")
    ap.add_argument("case", choices=sorted(CASE_SHAPE))
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--speed", type=float, default=4.0, help="ticks per second")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds before the case goes ACTIVE")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)
    serve(a.case, a.port, a.speed, a.delay, a.seed)


if __name__ == "__main__":
    main()
