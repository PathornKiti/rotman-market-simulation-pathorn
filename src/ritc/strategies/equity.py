"""
EQUITY TRADING - inventory-aware algorithmic market making.

The edge
--------
Post a bid and an ask around fair value and earn the spread (plus any passive
rebate) every time both sides trade. The risk is inventory: if the price moves
while you hold a position you can lose many spreads at once. This is a
simplified Avellaneda-Stoikov market maker:

    fair        = microprice (+ lean toward order-book imbalance)
    reservation = fair - skew * inventory          # shade quotes to shed inventory
    half_spread = max(min_half_spread, k * recent volatility)
    bid / ask   = reservation -/+ half_spread       # never cross the touch

Size shrinks on the side that would grow inventory, and past `hard_inventory`
we stop quoting that side and actively work the position down. A block the case
assigns to us (a position jump our quotes couldn't have filled) is cut at once
(`block_cut`): whoever dumped it usually knew something.

Time-series inputs (pricing/timeseries.py)
-----------------------------------------
* `vol` comes from an EWMA of tick price changes, or - with `vol_model = "garch"` -
  an online GARCH(1,1) on mid log returns, which widens spreads BEFORE the next
  burst of volatility when volatility clusters. Choose by A/B test in practice.
* `fair_shift` comes from the Ornstein-Uhlenbeck fair-value model. It is
  non-zero only when the stock is statistically mean-reverting, and then leans
  quotes toward where the price is expected to be `ou_horizon` ticks ahead.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from ..core.book import OrderBook
from ..core.bot import Snapshot, Strategy
from ..core.execution import QuoteManager
from ..pricing.news import parse_position_limit
from ..pricing.stats import ReturnVol
from ..pricing.timeseries import FairValueModel, OnlineGarch

log = logging.getLogger("ritc.equity")


@dataclass(frozen=True)
class QuoteParams:
    min_half_spread: float = 0.02
    vol_mult: float = 1.5
    skew_per_share: float = 0.000002     # $ shift of reservation price per share of inventory
    imbalance_lean: float = 0.25         # fraction of half-spread to lean toward book imbalance
    size: int = 2000
    max_inventory: int = 25000
    tick: float = 0.01


@dataclass(frozen=True)
class Quotes:
    bid: float | None
    ask: float | None
    bid_size: int
    ask_size: int
    fair: float
    reservation: float


def compute_quotes(book: OrderBook, inventory: int, vol: float, p: QuoteParams,
                   fair_shift: float = 0.0) -> Quotes | None:
    """
    Pure quoting function - all of the market maker's brain is here.
    `vol` is the per-tick price std in $; `fair_shift` the expected drift in $
    from a time-series fair-value model (0 when there is no reliable signal).
    """
    fair = book.microprice()
    if fair is None or book.best_bid is None or book.best_ask is None:
        return None
    half = max(p.min_half_spread, p.vol_mult * vol)
    fair += p.imbalance_lean * half * book.imbalance()
    fair += max(-half, min(half, fair_shift))          # never lean more than a half-spread
    reservation = fair - p.skew_per_share * inventory
    bid = reservation - half
    ask = reservation + half

    # Stay passive: never cross the opposite touch (we want the maker rebate).
    bid = min(bid, book.best_ask - p.tick)
    ask = max(ask, book.best_bid + p.tick)
    bid = round(math.floor(bid / p.tick + 1e-9) * p.tick, 6)       # floor to tick
    ask = round(math.ceil(ask / p.tick - 1e-9) * p.tick, 6)        # ceil to tick
    if ask <= bid:
        ask = bid + p.tick

    # Size asymmetry: shrink the side that adds inventory, stop at the cap.
    frac = max(-1.0, min(1.0, inventory / max(p.max_inventory, 1)))
    bid_size = int(p.size * max(0.0, 1 - frac)) if inventory < p.max_inventory else 0
    ask_size = int(p.size * max(0.0, 1 + frac)) if inventory > -p.max_inventory else 0
    bid_size = (bid_size // 100) * 100
    ask_size = (ask_size // 100) * 100
    return Quotes(bid if bid_size else None, ask if ask_size else None, bid_size, ask_size, fair, reservation)


def is_jump(prev_mid: float | None, mid: float | None, vol: float, k: float, floor: float) -> bool:
    """
    True when the mid moved more than max(k x per-tick vol, floor) in one tick.
    Outsized moves are where a market maker gets picked off: informed flow is
    trading, and stale quotes on the far side are the ones that fill.
    """
    if prev_mid is None or mid is None or k <= 0:
        return False
    return abs(mid - prev_mid) > max(k * vol, floor)


def ticks_to_close(tick: int, every: int) -> int:
    """Ticks until the next market close (0 = this tick is a close: 60, 120, ...)."""
    return -tick % every


def close_cuts(positions: dict[str, int], limit: float) -> list[tuple[str, str, int]]:
    """
    Orders that bring the aggregate position (sum of |position|) down to `limit`, largest
    positions first: every share over the limit at a close costs the same fine, and cutting the
    biggest inventory also cuts the most overnight-news risk.
    """
    excess = sum(abs(q) for q in positions.values()) - limit
    out = []
    for t, q in sorted(positions.items(), key=lambda kv: -abs(kv[1])):
        if excess <= 0 or not q:
            break
        n = int(min(abs(q), excess))
        out.append((t, "SELL" if q > 0 else "BUY", n))
        excess -= n
    return out


def inventory_reduction(inventory: int, hard: int, clip: int) -> tuple[str, int] | None:
    """Past the hard limit, cross the spread to get back under it."""
    if abs(inventory) <= hard:
        return None
    qty = min(abs(inventory) - hard // 2, clip)
    return ("SELL" if inventory > 0 else "BUY"), qty


class EquityStrategy(Strategy):
    name = "equity"

    def __init__(self, ctx):
        super().__init__(ctx)
        c, s = self.cfg.get("case", {}), self.cfg.get("strategy", {})
        self.tickers: list[str] = list(c.get("tickers", []))
        per = s.get("per_ticker", {})
        base = {k: v for k, v in s.items() if k in QuoteParams.__dataclass_fields__}
        self.params = {t: QuoteParams(**{**base, **per.get(t, {})}) for t in self.tickers}
        self.vol = {t: ReturnVol(s.get("vol_halflife", 20)) for t in self.tickers}
        self.vol_model = s.get("vol_model", "ewma")           # "ewma" or "garch"
        self.garch = {t: OnlineGarch(window=int(s.get("garch_window", 600))) for t in self.tickers}
        self.fv = {t: FairValueModel(horizon=s.get("ou_horizon", 10), ou_weight=s.get("ou_weight", 0.5))
                   for t in self.tickers}
        self.last_tick = -1
        self.hard = int(s.get("hard_inventory", 40000))
        # Jump guard: after an outsized move, pull quotes on that ticker for a few ticks
        # instead of re-quoting straight into informed flow.
        self.jump_k = float(s.get("jump_sigmas", 4.0))          # 0 = off
        self.jump_floor = float(s.get("jump_floor", 0.05))
        self.jump_pause = int(s.get("jump_pause_ticks", 2))
        self.prev_mid: dict[str, float | None] = {t: None for t in self.tickers}
        self.paused_until: dict[str, int] = {t: -1 for t in self.tickers}
        self.qm = QuoteManager(self.ex, tolerance=s.get("requote_tolerance", 0.01))
        # ASSIGNED BLOCKS: a position jump bigger than our quotes could have filled in one loop
        # is a block the case pushed onto us. Whoever dumped it usually knew something, so cut
        # `block_cut` of it at once (protective limit `block_slippage` through the touch)
        # instead of waiting for the inventory skew to work it off. 0 = off.
        self.block_cut = float(s.get("block_cut", 0.0))
        self.block_detect = int(s.get("block_detect", 5000))
        self.block_slippage = float(s.get("block_slippage", 0.03))
        self.prev_pos: dict[str, int] | None = None
        self.reduced: set[str] = set()           # tickers we crossed on last loop (not a block)
        # MARKET CLOSES (RITC 2026): every `close_every` ticks the aggregate position, sum of |position|
        # over every stock, is checked against a limit announced by news: $10 per share over it. From
        # `close_lead` ticks before a close we only quote the side that shrinks inventory; from
        # `close_cross` ticks before, we cross the spread for whatever is still over
        # `close_buffer` x limit. `aggregate_limit` is used until the news announces one. 0 = off.
        self.close_every = int(s.get("close_every", 0))
        self.close_lead = int(s.get("close_lead", 5))
        self.close_cross = int(s.get("close_cross", 2))
        self.close_buffer = float(s.get("close_buffer", 0.9))
        self.close_slippage = float(s.get("close_slippage", 0.05))
        self.agg_limit = int(s.get("aggregate_limit", 15000))
        self.wants_news = self.close_every > 0

    @property
    def book_tickers(self) -> list[str]:
        return self.tickers

    def step(self, snap: Snapshot) -> None:
        open_ids = None
        if not self.ex.dry_run:
            open_ids = {int(o["order_id"]) for o in self.client.orders("OPEN")}
        positions = snap.positions
        new_tick = snap.tick != self.last_tick       # sample time series once per tick, not per loop
        self.last_tick = snap.tick
        if self.block_cut:
            self.cut_blocks(snap, positions)
        closing = False
        if self.close_every:
            for n in self.new_news():
                lim = parse_position_limit(f"{n.get('headline', '')} {n.get('body', '')}")
                if lim:
                    log.info("AGGREGATE LIMIT %d shares at each close", lim)
                    self.agg_limit = lim
            left = ticks_to_close(snap.tick, self.close_every)
            closing = left <= self.close_lead
            if left <= self.close_cross:
                self.meet_close(snap, positions)
        for t in self.tickers:
            book = snap.book(t)
            if new_tick:
                if is_jump(self.prev_mid[t], book.mid, self.dollar_vol(t, book.mid), self.jump_k, self.jump_floor):
                    self.paused_until[t] = snap.abs_tick + self.jump_pause
                    log.info("JUMP %s %.3f -> %.3f: quotes off for %d ticks", t, self.prev_mid[t], book.mid,
                             self.jump_pause)
                self.prev_mid[t] = book.mid
                self.vol[t].update(book.mid)
                self.garch[t].add_price(book.mid)
                self.fv[t].update(book.mid)
            vol = self.dollar_vol(t, book.mid)
            fv = self.fv[t]
            shift = (fv.value - fv.kalman.level) if fv.mean_reverting and fv.value is not None else 0.0
            inv = positions.get(t, 0)

            red = inventory_reduction(inv, self.hard, self.ex.max_size(t))
            if red:
                touch = book.best_bid if red[0] == "SELL" else book.best_ask
                if touch:
                    self.reduced.add(t)
                    log.info("INVENTORY %s %+d -> %s %d", t, inv, *red)
                    self.ex.limit(t, red[0], red[1], touch - 0.02 if red[0] == "SELL" else touch + 0.02)

            q = compute_quotes(book, inv, vol, self.params[t], shift)
            if q is None or snap.abs_tick <= self.paused_until[t]:
                self.qm.sync(t, "BUY", None, 0, open_ids)
                self.qm.sync(t, "SELL", None, 0, open_ids)
                continue
            bid_sz = min(q.bid_size, self.risk.room(t, "BUY", positions))
            ask_sz = min(q.ask_size, self.risk.room(t, "SELL", positions))
            # Drawdown throttle shrinks only the side that ADDS inventory.
            if inv >= 0:
                bid_sz = self.sized(bid_sz) // 100 * 100
            if inv <= 0:
                ask_sz = self.sized(ask_sz) // 100 * 100
            if closing:              # into a close: only the side that shrinks inventory, no further
                bid_sz = min(bid_sz, max(0, -inv)) // 100 * 100
                ask_sz = min(ask_sz, max(0, inv)) // 100 * 100
            self.qm.sync(t, "BUY", q.bid, bid_sz, open_ids)
            self.qm.sync(t, "SELL", q.ask, ask_sz, open_ids)
            log.debug("%s inv %+d fair %.3f res %.3f  %s x %s", t, inv, q.fair, q.reservation, q.bid, q.ask)

    def meet_close(self, snap: Snapshot, positions: dict[str, int]) -> None:
        """Cross the spread for every share still over the aggregate limit (with a buffer)."""
        stocks = {t: q for t, q in positions.items()
                  if snap.securities.get(t, {}).get("type", "STOCK") == "STOCK"}
        for t, action, qty in close_cuts(stocks, self.close_buffer * self.agg_limit):
            book = snap.book(t)
            touch = book.best_bid if action == "SELL" else book.best_ask
            if touch:
                log.info("CLOSE %s %+d -> %s %d (aggregate limit %d)", t, positions[t], action, qty, self.agg_limit)
                self.reduced.add(t)
                self.ex.limit(t, action, qty, touch - self.close_slippage if action == "SELL"
                              else touch + self.close_slippage)

    def cut_blocks(self, snap: Snapshot, positions: dict[str, int]) -> None:
        prev, reduced = self.prev_pos, self.reduced
        self.prev_pos, self.reduced = dict(positions), set()
        if prev is None:
            return
        for t in self.tickers:
            jump = positions.get(t, 0) - prev.get(t, 0)
            if abs(jump) < self.block_detect or t in reduced:
                continue
            book = snap.book(t)
            inv = positions.get(t, 0)
            qty = min(int(abs(jump) * self.block_cut), abs(inv))
            touch = book.best_bid if inv > 0 else book.best_ask
            if qty <= 0 or touch is None or (jump > 0) != (inv > 0):
                continue
            action = "SELL" if inv > 0 else "BUY"
            log.info("BLOCK %s %+d assigned -> %s %d now", t, jump, action, qty)
            self.reduced.add(t)
            px = touch - self.block_slippage if action == "SELL" else touch + self.block_slippage
            self.ex.limit(t, action, qty, px)

    def dollar_vol(self, t: str, mid: float | None) -> float:
        """Per-tick price std in $. GARCH once warmed up, EWMA of price changes before that."""
        g = self.garch[t]
        if self.vol_model == "garch" and g.ready and mid:
            return g.vol() * mid
        return self.vol[t].std

    def wind_down(self, snap: Snapshot) -> None:
        self.qm.clear()
        self.ex.cancel_all()
        for t in self.tickers:
            pos = snap.positions.get(t, 0)
            book = snap.book(t)
            touch = book.best_bid if pos > 0 else book.best_ask
            if pos and touch:
                self.ex.limit(t, "SELL" if pos > 0 else "BUY", abs(pos), touch - 0.10 if pos > 0 else touch + 0.10)

    def on_stop(self) -> None:
        self.qm.live.clear()
