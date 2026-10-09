"""
Offline stand-in for the RIT Client REST API - one simulator, all five cases.

The real RIT client is Windows-only and you get no practice run on the day.
This serves the same endpoints on localhost:9999 with a simulated market so you
can prove a bot works end-to-end, tune thresholds and read the logs first:

    python -m ritc sim liability            # terminal 1
    python -m ritc run liability --live     # terminal 2 (live = against the SIM)

It is a SIMULATOR, not an emulator. Fills are approximate and the "other
traders" are noise, unless `--hostile` adds manipulative competitors (pump-and-dump,
spoofing, liquidity vacuums, penny-jumping, crowded tenders; docs/HOSTILE_MARKET.md).
Use it to debug logic and plumbing, not to predict a score.
The tickers it creates match the default files in config/, so both work out of
the box.

Where an official RITC case package states a rule, the simulator follows it
(see docs/OFFICIAL_RULES.md): derivatives fees, spreads, strikes, limits, news
wording and the delta-limit penalty; the ETF case's USD-quoted ETF, 2x limit
weight, rebates and close-out at fair value.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from ..pricing.options import bs_greeks, bs_price

TRADER_ID = "SIMBOT"
TICK = 0.01
# QUEUE MODE (`--queue`): share of the displayed queue ahead of a resting order that is cancelled
# each tick. An assumption; the case packages give nothing on queue dynamics.
QUEUE_CANCEL = 0.2


def delta_penalty(delta: float, limit: float, pct: float) -> float:
    """Official volatility-case fine for one second: (|delta| - limit) x pct when over the limit."""
    return max(0.0, abs(delta) - limit) * pct


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
    ccy: str = "CAD"             # quote currency; anything but CAD settles into that currency's position
    tick: float = TICK           # price increment (currencies quote to 4 decimals)
    position: int = 0
    cost: float = 0.0            # signed cost basis for vwap
    volume: int = 0
    last: float = 0.0
    bids: list[list[float]] = field(default_factory=list)   # [price, qty]
    asks: list[list[float]] = field(default_factory=list)
    tradeable: bool = True
    hist: list[dict] = field(default_factory=list)

    def rebuild(self, rng: random.Random, levels: int = 10) -> None:
        tk, nd = self.tick, max(2, round(-math.log10(self.tick)))
        half = max(self.spread / 2, tk)
        bb = math.floor((self.mid - half) / tk + 1e-9) * tk
        ba = math.ceil((self.mid + half) / tk - 1e-9) * tk
        if ba <= bb:
            ba = bb + tk
        if not self.tradeable:
            self.bids, self.asks = [], []
            return
        bb, ba = max(bb, tk), max(ba, 2 * tk)
        self.bids = [[round(bb - i * tk * (1 + i // 3), nd), self.lot * rng.randint(1, 4)] for i in range(levels)]
        self.bids = [lv for lv in self.bids if lv[0] >= tk]
        self.asks = [[round(ba + i * tk * (1 + i // 3), nd), self.lot * rng.randint(1, 4)] for i in range(levels)]


@dataclass
class Episode:
    """One manipulation of a mid: ramp to `amp` over `up` ticks, hold, decay to `residual` x amp."""
    start: int
    amp: float
    up: int
    hold: int
    down: int
    residual: float = 0.0

    def offset(self, now: int) -> float:
        e = now - self.start
        if e <= self.up:
            return self.amp * e / max(self.up, 1)
        e -= self.up + self.hold
        if e <= 0:
            return self.amp
        frac = min(1.0, e / max(self.down, 1))
        return self.amp * (1 - frac * (1 - self.residual))

    def done(self, now: int) -> bool:
        return now - self.start > self.up + self.hold + self.down


class Market:
    def __init__(self, case: str, ticks_per_period: int, periods: int, seed: int | None, hostile: float = 0.0,
                 queue: bool = False):
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
        self.cash = 0.0              # CAD; other currencies are held as the position of that currency's security
        self.penalty = 0.0           # fines the scoring committee deducts outside RIT's P&L (delta limit)
        self.secs: dict[str, Sec] = {}
        self.orders: dict[int, dict] = {}
        self.done_orders: list[dict] = []
        self.news: list[dict] = []
        self.tenders: dict[int, dict] = {}
        self.limits: list[dict] = []
        self.ids = itertools.count(1)
        self.lock = threading.RLock()
        self.state: dict = {}
        # HOSTILE MODE (0 = off): other teams are market makers and manipulators, not noise.
        # See `_adversary`. Everything is drawn from its own generators, so with hostile = 0
        # the market is exactly the benign one, and the base price path's draws never shift.
        self.hostile = max(0.0, float(hostile))
        self.adv_rng = random.Random(f"{self.fill_seed}:adversary")
        self.episodes: dict[str, list[Episode]] = {}
        self.adv_off: dict[str, float] = {}          # current manipulation offset of each mid
        self.spoofs: dict[str, tuple[str, int, int]] = {}   # ticker -> (side, until tick, fake qty)
        self.vacuums: dict[str, int] = {}            # ticker -> until tick
        self.pumps: dict[str, int] = {}              # ticker -> tick its pump-and-dump ends
        self.crowded: set[int] = set()               # tender ids the crowd has already reacted to
        # Which threats are on (for attributing damage): RITC_HOSTILE_THREATS="pump,spoof,vacuum,jump,crowd"
        self.threats = set((os.environ.get("RITC_HOSTILE_THREATS") or "pump,spoof,vacuum,jump,crowd").split(","))
        # QUEUE MODE (off = the old fill model, unchanged): price-time priority against the displayed
        # book. A resting order joins BEHIND the shares already shown at its price; passive flow eats
        # that queue first. Improving the touch puts us first. `ahead` = shares in front of each order.
        self.queue = bool(queue)
        self.ahead: dict[int, float] = {}
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
        # Official Algo case (RITC 2019/2020/2023): BULL $10 + BEAR $15 in CAD, RITC $25 quoted in USD,
        # P_RITC,USD * USD = P_BULL + P_BEAR. $0.02/share taker fee, $0.01 passive rebate. The ETF
        # counts 2x towards the limits and closes out at fair value (the basket converted).
        self.secs["USD"] = Sec("USD", 1.0, spread=0.0002, tick=0.0001, sigma=0.0003, fee=0.0, lot=1_000_000,
                               max_trade=2_500_000, kind="CURRENCY")
        self.secs["BULL"] = Sec("BULL", 10.0, sigma=0.03, fee=0.02, rebate=0.01, lot=2000)
        self.secs["BEAR"] = Sec("BEAR", 15.0, sigma=0.03, fee=0.02, rebate=0.01, lot=2000)
        self.secs["RITC"] = Sec("RITC", 25.0, sigma=0.0, fee=0.02, rebate=0.01, lot=1500, spread=0.05, ccy="USD")
        self.state["premium"] = 0.0
        # Private RITC tender offers (official: "participants will also receive private tender offers for the
        # ETF"). Drawn from their own generator so the price path is the same as without tenders.
        self.tender_rng = random.Random(f"{self.fill_seed}:etf-tenders")
        self.limits = [{"name": "equity", "gross": 0, "net": 0, "gross_limit": 300_000, "net_limit": 200_000,
                        "_weights": {"RITC": 2.0, "BULL": 1.0, "BEAR": 1.0}}]

    def _etf_nav_usd(self) -> float:
        return (self.secs["BULL"].mid + self.secs["BEAR"].mid) / self.secs["USD"].mid

    def _setup_equity(self) -> None:
        for t, px, sg in (("SPNG", 15.0, 0.02), ("SMMR", 25.0, 0.03), ("ATMN", 30.0, 0.04)):
            self.secs[t] = Sec(t, px, spread=0.06, sigma=sg, fee=0.02, rebate=0.01, lot=1000)
        self.limits = [{"name": "equity", "gross": 0, "net": 0, "gross_limit": 200_000, "net_limit": 100_000}]

    def _setup_derivatives(self) -> None:
        # Official Volatility case (RITC 2019/2020/2023): RTM $50, 1- and 2-month calls/puts at strikes
        # 45..54, market makers always quote a 2-cent spread for very large size, $0.02/share and
        # $2.00/contract fees, limits RTM 50k/50k shares and options 2,500 gross / 1,000 net.
        self.state.update(true_vol=0.20, next_vol=0.20, mkt_vol=0.20, week=-1,
                          delta_limit=7000, penalty_pct=0.005)
        self.secs["RTM"] = Sec("RTM", 50.0, spread=0.02, lot=20_000, fee=0.02, max_trade=10_000)
        for exp, exp_tick in (("1", 300), ("2", 600)):
            for k in range(45, 55):
                for cp in "CP":
                    t = f"RTM{exp}{cp}{k}"
                    self.secs[t] = Sec(t, 1.0, spread=0.02, lot=500, fee=2.00, mult=100, kind="OPTION",
                                       max_trade=100)
                    self.state[t] = (exp_tick, cp == "C", float(k))
        options = {t: 1.0 for t, s in self.secs.items() if s.kind == "OPTION"}
        self.limits = [{"name": "options", "gross": 0, "net": 0, "gross_limit": 2500, "net_limit": 1000,
                        "_weights": options},
                       {"name": "etf", "gross": 0, "net": 0, "gross_limit": 50_000, "net_limit": 50_000,
                        "_weights": {"RTM": 1.0}}]
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
            for t, off in self.adv_off.items():      # manipulation sits on top of the true price
                self.secs[t].mid -= off
            for s in self.secs.values():
                if s.sigma:
                    s.mid = max(0.5, s.mid + self.rng.gauss(0, s.sigma))
            getattr(self, f"_tick_{self.case}")()
            if self.hostile:
                self._adversary()
            for s in self.secs.values():
                s.last = s.mid
                s.rebuild(self.rng)
                s.hist.append({"period": self.period, "tick": self.tick, "open": s.mid, "high": s.mid,
                               "low": s.mid, "close": round(s.mid, 4)})
            if self.hostile:
                self._hostile_books()
            if self.queue:
                self._decay_queues()
            self._fill_resting()

    # ===================================================== hostile participants
    def _adv_targets(self) -> list[str]:
        return [t for t, s in self.secs.items() if s.kind == "STOCK" and s.tradeable]

    def _adv_sigma(self, s: Sec) -> float:
        """Typical one-tick move of a ticker, the unit manipulation sizes are drawn in."""
        if s.sigma:
            return s.sigma
        if s.ticker == "RITC":
            return 0.06
        return s.mid * self.state.get("true_vol", 0.2) / 60       # RTM: annual vol over sqrt(3600) ticks

    def _adversary(self) -> None:
        """
        Other teams, modelled as the things that hurt a bot in a live heat:

        * PUMP AND DUMP (momentum ignition): someone buys (sells) aggressively for a few
          ticks, the price runs 5-10 sigma, then they unload and it comes all the way back.
          A bot that chases, or is forced to liquidate at the top, pays for it.
        * SPOOFING: a large fake order a tick behind the touch (see `_hostile_books`). The
          crowd believes it and the price leans that way, then it is pulled and the lean
          reverts. Our aggressive orders can never fill against it: it was never real.
        * LIQUIDITY VACUUM: market makers pull their quotes for a few ticks. The mid does not
          move, but the touch jumps far out and is thin. A bot that crosses then pays 10x.
        * CROWDED TENDERS: everyone gets the same institutional tender and unwinds it into
          the same book at the same time, so the price runs against the unwind and only
          half comes back.
        * COMPETING ARBITRAGEURS (ETF): other desks close most of any ETF/basket gap a
          manipulation opens, before we see it.
        * PENNY-JUMPING competitors live in `_hostile_books`: they react to our resting quotes.

        All of it is drawn from `adv_rng` in a fixed order, independent of what our bot does,
        so every setting being compared meets the same manipulation.
        """
        h, r, now = self.hostile, self.adv_rng, self.abs_tick
        for t in self._adv_targets():
            s = self.secs[t]
            sig = self._adv_sigma(s)
            if r.random() < 0.006 * h and self.pumps.get(t, -1) < now and "pump" in self.threats:
                d = r.choice([-1, 1])
                e = Episode(now, d * r.uniform(5, 10) * sig, r.randint(4, 8), r.randint(0, 2), r.randint(6, 15))
                self.episodes.setdefault(t, []).append(e)
                self.pumps[t] = now + e.up + e.hold + e.down
            if r.random() < 0.03 * h and t not in self.spoofs and "spoof" in self.threats:
                side, n = r.choice(["BUY", "SELL"]), r.randint(3, 8)
                self.spoofs[t] = (side, now + n, int(s.lot * r.uniform(8, 15)))
                lean = (1 if side == "BUY" else -1) * 1.5 * sig
                self.episodes.setdefault(t, []).append(Episode(now, lean, n, 0, 2))
            if r.random() < 0.004 * h and t not in self.vacuums and "vacuum" in self.threats:
                self.vacuums[t] = now + r.randint(2, 4)
        for tid, tender in list(self.tenders.items()):
            if tid in self.crowded or "crowd" not in self.threats:
                continue
            self.crowded.add(tid)
            unwind = -1 if tender["action"] == "BUY" else 1        # we BUY from the client -> the crowd SELLS
            q = tender["quantity"]
            if self.case == "liability":
                s = self.secs[tender["ticker"]]
                amp = unwind * h * 3 * (s.impact or 2e-6) * q    # three other desks with the same block
                self.episodes.setdefault(s.ticker, []).append(
                    Episode(now, amp, r.randint(6, 12), 3, 40, residual=0.5))
            elif self.case == "etf":
                # The crowd hedges its ETF tender in the basket: the opposite way to its ETF leg.
                for t in ("BULL", "BEAR"):
                    self.episodes.setdefault(t, []).append(
                        Episode(now, unwind * h * 0.03 * q / 10_000, r.randint(2, 4), 2, 20, residual=0.3))
        self.spoofs = {t: v for t, v in self.spoofs.items() if v[1] >= now}
        self.vacuums = {t: u for t, u in self.vacuums.items() if u >= now}
        for t in list(self.episodes):
            self.episodes[t] = [e for e in self.episodes[t] if not e.done(now) or e.residual]
            off = sum(e.offset(now) for e in self.episodes[t])
            s = self.secs[t]
            s.mid = max(0.5, s.mid + off)
            self.adv_off[t] = off
        if self.case == "etf":
            # Everyone else arbitrages too: competing desks close 70% of any ETF/basket gap a
            # manipulation of BULL or BEAR opens, before we see it.
            follow = 0.7 * sum(self.adv_off.get(t, 0.0) for t in ("BULL", "BEAR")) / self.secs["USD"].mid
            ritc = self.secs["RITC"]
            ritc.mid += follow
            self.adv_off["RITC"] = sum(e.offset(now) for e in self.episodes.get("RITC", [])) + follow
        if self.case == "derivatives":
            self._price_options()          # option market makers price off the RTM they see

    def _hostile_books(self) -> None:
        """Book-level manipulation, after the honest books are rebuilt: vacuums, spoofs, penny-jumps."""
        now = self.abs_tick
        for t, _ in self.vacuums.items():
            s = self.secs[t]
            gap = max(8 * s.tick, 3 * s.spread)
            thin = max(1, s.lot // 4)
            s.bids = [[lv[0], thin] for lv in s.bids if lv[0] <= s.mid - gap] or s.bids[-1:]
            s.asks = [[lv[0], thin] for lv in s.asks if lv[0] >= s.mid + gap] or s.asks[-1:]
        for t, (side, _, qty) in self.spoofs.items():
            s = self.secs[t]
            levels = s.bids if side == "BUY" else s.asks
            if levels:
                px = round(levels[0][0] + (-s.tick if side == "BUY" else s.tick), 6)
                levels.insert(1, [px, qty, "spoof"])
        # Penny-jumping: a competing market maker steps one tick in front of our resting quote
        # whenever the spread (and its own edge) leaves room, so the passive flow goes to them first. Keyed on
        # (seed, tick, ticker, side) like the passive flow, so settings meet the same competitors.
        best: dict[tuple[str, str], float] = {}
        for o in self.orders.values():
            k = (o["ticker"], o["action"])
            p = o["price"]
            best[k] = max(best.get(k, p), p) if o["action"] == "BUY" else min(best.get(k, p), p)
        for (t, action), p in sorted(best.items()):
            s = self.secs[t]
            if not s.tradeable or not s.bids or not s.asks:
                continue
            r = random.Random(f"{self.fill_seed}:{now}:{t}:{action}:jump")
            if r.random() >= min(1.0, 0.6 * self.hostile) or "jump" not in self.threats:
                continue
            # A rational competitor keeps at least a tick of edge to the mid: it never jumps through fair.
            jump = round(p + s.tick, 6) if action == "BUY" else round(p - s.tick, 6)
            if action == "BUY" and p >= s.bids[0][0] and jump < s.asks[0][0] - 1e-9 and jump <= s.mid - s.tick:
                s.bids.insert(0, [jump, s.lot])
            elif action == "SELL" and p <= s.asks[0][0] and jump > s.bids[0][0] + 1e-9 and jump >= s.mid + s.tick:
                s.asks.insert(0, [jump, s.lot])

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
            # Competitive auctions: the client's hidden reserve is 0-15 cents through the mid, drawn
            # per tender from its own generator (the market path and tender stream are unchanged).
            off = random.Random(f"{self.fill_seed}:{self.abs_tick}:{t}:reserve").uniform(0.0, 0.15)
            tid = next(self.ids)
            self.tenders[tid] = {
                "tender_id": tid, "period": self.period, "tick": self.tick, "expires": self.abs_tick + 10,
                "caption": f"Client wants you to {action} {qty} {t}", "quantity": qty, "action": action,
                "is_fixed_bid": fixed, "price": price if fixed else None, "ticker": t,
                "_reserve": round(s.mid - off if action == "BUY" else s.mid + off, 2),
            }

    def _tick_etf(self) -> None:
        p = 0.92 * self.state["premium"] + self.rng.gauss(0, 0.06)
        self.state["premium"] = p
        self.secs["RITC"].mid = self._etf_nav_usd() + p
        for tid in [k for k, v in self.tenders.items() if v["expires"] <= self.abs_tick]:
            self.tenders.pop(tid)
        r = self.tender_rng
        if self.abs_tick % 20 == 7 and self.abs_tick < self.tpp * self.periods - 20:
            # The package gives no sizes or prices; these are a guess: a fixed price near the
            # market (USD), sometimes better than the basket, sometimes worse, open for 15 ticks.
            action = r.choice(["BUY", "SELL"])
            qty = r.choice([5_000, 10_000, 20_000, 30_000])
            edge = r.uniform(-0.10, 0.20)
            s = self.secs["RITC"]
            tid = next(self.ids)
            self.tenders[tid] = {
                "tender_id": tid, "period": self.period, "tick": self.tick, "expires": self.abs_tick + 15,
                "caption": f"Client wants you to {action} {qty} RITC", "quantity": qty, "action": action,
                "is_fixed_bid": True, "price": round(s.mid - edge if action == "BUY" else s.mid + edge, 2),
                "ticker": "RITC",
            }

    def _tick_equity(self) -> None:
        if self.rng.random() < 0.02:                   # occasional informed jump
            s = self.rng.choice(list(self.secs.values()))
            s.mid += self.rng.choice([-1, 1]) * self.rng.uniform(0.10, 0.30)
        # BLOCK TRANSFERS (DEVLOG item 6): the case assigns market makers an unhedged block at the
        # mid, unannounced - the bot only sees its position jump. Whoever dumped it knew something:
        # the price then drifts 2-5 sigma against the holder over 10 ticks. Own generator, so the
        # price path's draws are unchanged; `blocks = False` turns it off.
        drift = self.state.setdefault("block_drift", [])
        for d in drift:
            self.secs[d[1]].mid += d[2]
            d[0] -= 1
        self.state["block_drift"] = [d for d in drift if d[0] > 0]
        r = random.Random(f"{self.fill_seed}:{self.abs_tick}:block")
        if self.state.get("blocks", True) and r.random() < 0.01:
            t, side, qty = r.choice(sorted(self.secs)), r.choice([1, -1]), r.choice([5_000, 10_000, 15_000])
            s = self.secs[t]
            s.position += side * qty
            s.cost += side * qty * s.mid
            self.cash -= side * qty * s.mid
            self.state["block_drift"].append([10, t, -side * r.uniform(2, 5) * s.sigma / 10])

    def _tick_derivatives(self) -> None:
        week = (self.abs_tick - 1) // 75
        if week != self.state["week"]:
            self.state["week"] = week
            if week == 0:
                self._news("Delta limit", f"The delta limit for this heat is {self.state['delta_limit']:,} and the "
                           f"penalty percentage is {100 * self.state['penalty_pct']:g}%")
                self.state["next_vol"] = self.rng.choice([0.15, 0.20, 0.25, 0.30, 0.35])
            prev = self.state["true_vol"]
            # Next week's vol is drawn a week in advance, so the mid-week analyst range is about the
            # vol that will actually happen (as in the real case), not a copy of this week's.
            self.state["true_vol"] = self.state["next_vol"]
            self.state["next_vol"] = self.rng.choice([0.15, 0.20, 0.25, 0.30, 0.35])
            # The market only half-believes the news: implied vol lags true vol.
            self.state["mkt_vol"] = 0.5 * prev + 0.5 * self.state["true_vol"]
            self._news(f"Volatility week {week + 1}",
                       f"The realized volatility of RTM for this week will be {round(self.state['true_vol'] * 100)}%")
        if (self.abs_tick - 1) % 75 == 37:
            v = round(self.state["next_vol"] * 100)
            lo = v - self.rng.randint(0, 3)           # official form: a 3-point range containing the truth
            self._news("Volatility forecast",
                       f"The realized volatility of RTM for next week will be between {lo}-{lo + 3}%")
        self.state["mkt_vol"] += 0.05 * (self.state["true_vol"] - self.state["mkt_vol"]) + self.rng.gauss(0, 0.002)
        rtm = self.secs["RTM"]
        rtm.mid = max(1.0, rtm.mid * math.exp(self.rng.gauss(0, self.state["true_vol"] * math.sqrt(1 / 3600))))
        self._price_options()
        self.penalty += delta_penalty(self.portfolio_delta(), self.state["delta_limit"], self.state["penalty_pct"])

    def portfolio_delta(self) -> float:
        """Shares-equivalent delta of RTM + options (BS delta at the market makers' vol)."""
        S = self.secs["RTM"].mid
        d = float(self.secs["RTM"].position)
        for t, s in self.secs.items():
            if s.kind != "OPTION" or not s.position or not s.tradeable:
                continue
            exp, is_call, K = self.state[t]
            T = max(exp - self.abs_tick, 1) / 3600
            d += s.position * s.mult * bs_greeks(S, K, T, 0.0, self.state["mkt_vol"], is_call).delta
        return d

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
        self._settle(s.ccy, -sign * qty * px * s.mult + (s.rebate if maker else -s.fee) * qty)
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
            if len(lv) > 2:            # a spoof: pulled before any order can reach it
                continue
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
            if self.queue:
                self.ahead[oid] = self._shown(s, action, price)
        else:
            order["status"] = "TRANSACTED"
            self.done_orders.append(order)
        return order

    @staticmethod
    def _shown(s: Sec, action: str, price: float) -> float:
        """Real (non-spoof) displayed shares on our side at exactly `price`."""
        levels = s.bids if action == "BUY" else s.asks
        return float(sum(lv[1] for lv in levels if len(lv) == 2 and abs(lv[0] - price) < s.tick / 2))

    def _decay_queues(self) -> None:
        """
        After the books are rebuilt: part of the queue ahead of each order cancels; if its price
        level is no longer shown (the book moved inside or away from it), nobody is left in front.
        New shares shown at the level arrived after us and queue behind.
        """
        for oid, o in self.orders.items():
            if self.ahead.get(oid):
                s = self.secs[o["ticker"]]
                shown = self._shown(s, o["action"], o["price"])
                self.ahead[oid] = self.ahead[oid] * (1 - QUEUE_CANCEL) if shown else 0.0

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
                if self.ahead.get(oid):            # queue mode: the shares in front trade first
                    eat = min(flow[key], self.ahead[oid])
                    self.ahead[oid] -= eat
                    flow[key] -= eat
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
                    self.ahead.pop(oid, None)

    def cancel(self, oid: int) -> bool:
        o = self.orders.pop(oid, None)
        self.ahead.pop(oid, None)
        if o:
            o["status"] = "CANCELLED"
            self.done_orders.append(o)
        return o is not None

    def _settle(self, ccy: str, amount: float) -> None:
        """Cash moves in the quote currency: CAD is cash, a foreign balance is that currency's position."""
        if ccy == "CAD":
            self.cash += amount
        else:
            self.secs[ccy].position += amount

    def _fx(self, ccy: str) -> float:
        return 1.0 if ccy == "CAD" else self.secs[ccy].mid

    def _close_out(self) -> None:
        for s in self.secs.values():
            if s.position and s.tradeable and s.kind != "CURRENCY":
                # The official ETF case closes the ETF at fair value: the basket converted.
                px = self._etf_nav_usd() if self.case == "etf" and s.ticker == "RITC" else s.mid
                self._settle(s.ccy, s.position * px * s.mult)
                s.position = 0

    # ===================================================== views
    def nlv(self) -> float:
        """Net liquidation value in CAD (foreign positions and balances at the current rate)."""
        return self.cash + sum(s.position * s.mid * s.mult * (1.0 if s.kind == "CURRENCY" else self._fx(s.ccy))
                               for s in self.secs.values() if s.tradeable)

    def score(self) -> float:
        """What the judges rank: NLV less the fines charged outside RIT's P&L."""
        return self.nlv() - self.penalty

    def sec_view(self, s: Sec) -> dict:
        bid, ask = (s.bids[0][0] if s.bids else None), (s.asks[0][0] if s.asks else None)
        return {"ticker": s.ticker, "type": s.kind, "position": s.position,
                "vwap": abs(s.cost / s.position) if s.position else 0.0,
                "last": round(s.last, 4), "bid": bid, "ask": ask,
                "bid_size": s.bids[0][1] if s.bids else 0, "ask_size": s.asks[0][1] if s.asks else 0,
                "volume": s.volume, "trading_fee": s.fee, "limit_order_rebate": s.rebate,
                "max_trade_size": s.max_trade, "multiplier": s.mult, "is_tradeable": s.tradeable, "currency": s.ccy,
                "unrealized": round((s.mid * s.position - s.cost) * s.mult, 2)}

    def book_view(self, s: Sec, limit: int) -> dict:
        def rows(levels, action):
            return [{"order_id": 0, "trader_id": "ANON", "ticker": s.ticker, "price": lv[0], "quantity": lv[1],
                     "quantity_filled": 0, "action": action, "status": "OPEN"} for lv in levels[:limit] if lv[1] > 0]
        mine_b = [o for o in self.orders.values() if o["ticker"] == s.ticker and o["action"] == "BUY"]
        mine_a = [o for o in self.orders.values() if o["ticker"] == s.ticker and o["action"] == "SELL"]
        bids = sorted(rows(s.bids, "BUY") + mine_b, key=lambda r: -r["price"])
        asks = sorted(rows(s.asks, "SELL") + mine_a, key=lambda r: r["price"])
        return {"bid": bids[:limit], "ask": asks[:limit]}

    def limits_view(self) -> list[dict]:
        out = []
        for lim in self.limits:
            w = lim.get("_weights") or {t: 1.0 for t, s in self.secs.items() if s.kind != "CURRENCY"}
            live = [(s.position, w[t]) for t, s in self.secs.items() if t in w and s.tradeable]
            out.append({**{k: v for k, v in lim.items() if not k.startswith("_")},
                        "gross": sum(abs(q) * x for q, x in live), "net": sum(q * x for q, x in live)})
        return out


# ========================================================= HTTP layer
def route(m: Market, method: str, path: str, q: dict[str, str]) -> tuple[int, object]:
    """One API call against the simulated market: (HTTP status, JSON body). Shared by the HTTP
    server and the tuner's in-process transport (`InProcessAdapter`), so both answer the same."""
    path = path[3:] if path.startswith("/v1") else path
    path = path.rstrip("/")
    with m.lock:
        if method == "GET":
            if path == "/case":
                return 200, {"name": f"SIM-{m.case.upper()}", "period": m.period, "tick": m.tick,
                             "ticks_per_period": m.tpp, "total_periods": m.periods, "status": m.status}
            if path == "/trader":
                return 200, {"trader_id": TRADER_ID, "first_name": "Sim", "last_name": "Bot", "nlv": round(m.nlv(), 2)}
            if path == "/limits":
                return 200, m.limits_view()
            if path == "/securities":
                t = q.get("ticker")
                return 200, [m.sec_view(s) for s in m.secs.values() if not t or s.ticker == t]
            if path == "/securities/book":
                s = m.secs.get(q.get("ticker", ""))
                return (200, m.book_view(s, int(q.get("limit", 20)))) if s else (404, {})
            if path == "/securities/history":
                s = m.secs.get(q.get("ticker", ""))
                rows = s.hist[::-1] if s else []
                return 200, rows[: int(q["limit"])] if q.get("limit") else rows
            if path == "/securities/tas":
                return 200, []
            if path == "/news":
                since = int(q.get("since", 0) or 0)
                items = [n for n in m.news if n["news_id"] > since][::-1]
                return 200, items[: int(q.get("limit", 50))]
            if path == "/orders":
                st = q.get("status", "OPEN")
                return 200, list(m.orders.values()) if st == "OPEN" else [o for o in m.done_orders if o["status"] == st]
            if path == "/tenders":
                return 200, [{k: v for k, v in t.items() if not k.startswith("_")} for t in m.tenders.values()]
            if path == "/leases":
                return 200, []
        elif method == "POST":
            if m.status != "ACTIVE" and (path == "/orders" or path.startswith("/tenders/")):
                return 400, {"code": "CASE_NOT_ACTIVE"}
            if path == "/orders":
                try:
                    price = float(q["price"]) if q.get("price") else None
                    return 200, m.submit(q["ticker"], q.get("type", "MARKET").upper(), int(float(q["quantity"])),
                                         q["action"].upper(), price)
                except (KeyError, ValueError) as exc:
                    return 422, {"code": "BAD_ORDER", "message": str(exc)}
            if path == "/commands/cancel":
                t = q.get("ticker")
                ids = [oid for oid, o in m.orders.items() if q.get("all") or not t or o["ticker"] == t]
                for oid in ids:
                    m.cancel(oid)
                return 200, {"cancelled_order_ids": ids}
            if path.startswith("/tenders/"):
                t = m.tenders.pop(int(path.rsplit("/", 1)[1]), None)
                if t is None:
                    return 404, {"code": "TENDER_NOT_FOUND"}
                price = t["price"]
                if not t["is_fixed_bid"]:
                    price = float(q.get("price", 0))
                    # Official: any bid PAST the hidden reserve fills at our price. When we BUY the
                    # client is selling, so it takes bids at or above its reserve (and vice versa).
                    good = price >= t["_reserve"] if t["action"] == "BUY" else price <= t["_reserve"]
                    if not good:
                        return 200, {"success": False}
                s = m.secs[t["ticker"]]
                sign = 1 if t["action"] == "BUY" else -1
                s.position += sign * t["quantity"]
                s.cost += sign * t["quantity"] * price
                m._settle(s.ccy, -sign * t["quantity"] * price)
                return 200, {"success": True}
        elif method == "DELETE":
            if path.startswith("/orders/"):
                ok = m.cancel(int(path.rsplit("/", 1)[1]))
                return (200 if ok else 404), {"success": ok}
            if path.startswith("/tenders/"):
                m.tenders.pop(int(path.rsplit("/", 1)[1]), None)
                return 200, {"success": True}
    return 404, {"code": "NOT_FOUND", "message": path}


def make_handler(m: Market):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):        # keep the console quiet
            pass

        def _handle(self, method: str) -> None:
            u = urlparse(self.path)
            code, obj = route(m, method, u.path, {k: v[0] for k, v in parse_qs(u.query).items()})
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_DELETE(self):
            self._handle("DELETE")

    return H


class InProcessAdapter(requests.adapters.BaseAdapter):
    """
    A `requests` transport that answers from the Market in this process instead of over a
    socket. The tuner mounts it on the bot's client: same `route` as the HTTP server, so
    results are identical, but no loopback HTTP (which antivirus software can slow ~60x).
    """

    def __init__(self, market: Market):
        super().__init__()
        self.m = market

    def send(self, request, **kwargs):
        u = urlparse(request.url)
        code, obj = route(self.m, request.method, u.path, {k: v[0] for k, v in parse_qs(u.query).items()})
        r = requests.Response()
        r.status_code, r._content, r.url, r.request = code, json.dumps(obj).encode(), request.url, request
        r.headers["Content-Type"] = "application/json"
        r.encoding = "utf-8"
        return r

    def close(self) -> None:
        pass


CASE_SHAPE = {          # ticks_per_period, periods
    "liability": (300, 1), "etf": (300, 1), "equity": (300, 1),
    "derivatives": (300, 2), "commodity": (300, 2),
}


def serve(case: str, port: int = 9999, speed: float = 4.0, delay: float = 2.0, seed: int | None = None,
          block: bool = True, hostile: float = 0.0, queue: bool = False) -> tuple[ThreadingHTTPServer, Market]:
    tpp, periods = CASE_SHAPE[case]
    m = Market(case, tpp, periods, seed, hostile, queue)
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
        fine = f"  penalties {m.penalty:,.2f}  score {m.score():,.2f}" if m.penalty else ""
        print(f"[sim] case finished. Final NLV {m.nlv():,.2f}{fine}")

    th = threading.Thread(target=clock, daemon=True)
    th.start()
    if block:
        print(f"[sim] {case} on http://127.0.0.1:{port}/v1  ({tpp} ticks x {periods} periods, {speed} ticks/s"
              f"{f', hostile {hostile:g}' if hostile else ''}{', queue' if queue else ''})")
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
    ap.add_argument("--hostile", type=float, default=0.0,
                    help="0 = noise traders only; 1 = manipulators (see Market._adversary)")
    ap.add_argument("--queue", action="store_true", help="price-time priority behind the displayed book")
    a = ap.parse_args(argv)
    serve(a.case, a.port, a.speed, a.delay, a.seed, hostile=a.hostile, queue=a.queue)


if __name__ == "__main__":
    main()
