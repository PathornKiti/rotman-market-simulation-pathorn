"""
Block execution: work a large position to a target over time.

Dumping a block (a 50,000-share tender, an end-of-period flatten) in one go
walks deep into the book. Working it over time lets the book refill, and
posting part of it PASSIVELY earns the spread instead of paying it. This is a
schedule-driven "passive-then-aggressive" algo with an iceberg display:

    schedule   linear (optionally front-loaded) path from start to target by the deadline
    on/ahead   rest a passive child at the touch (or one tick inside a wide spread),
               showing at most `display_qty` (iceberg) -> earn the spread
    behind     cross the spread for the shortfall, sized to the book so the
               slice VWAP stays within `max_slippage` of the touch
    urgent     in the last `urgent_ticks`, cross for everything left
    limit      never trade worse than `limit_price` (e.g. the tender price)

Progress is measured from the POSITION, not from order acks, so partial fills,
resting fills and manual trades are all accounted for automatically.

Almgren-Chriss schedule (`kappa > 0`)
-------------------------------------
Almgren & Chriss (2000), "Optimal execution of portfolio transactions": with
temporary impact eta ($ per share, per share/tick traded), price vol sigma
($ per share per sqrt(tick)) and risk aversion lambda (1/$), the mean-variance
optimal holding path to the deadline T is

    x(t) / X = sinh(kappa (T - t)) / sinh(kappa T),   kappa = sqrt(lambda sigma^2 / eta)

kappa*T -> 0 is a straight line (minimise impact); a large kappa*T front-loads
(minimise price risk). It replaces the hand-tuned `front_load` curve with one
derived from the market: sigma from GARCH, eta from the visible book density
(`book_eta`), so a volatile, deep market unwinds faster and a calm, thin one
slower.
"""

from __future__ import annotations

import logging
import math
from contextlib import nullcontext
from dataclasses import dataclass

from .book import OrderBook

log = logging.getLogger("ritc.algo")


@dataclass(frozen=True)
class AlgoParams:
    max_slippage: float = 0.04       # aggressive slices: VWAP within this of the touch
    participation: float = 0.35      # max fraction of visible depth taken per slice
    display_qty: int = 5000          # iceberg: largest passive child shown
    urgent_ticks: int = 10           # cross everything in the final ticks
    front_load: float = 0.0          # 0 = linear schedule; 1 = strongly front-loaded
    improve_if_spread_ticks: int = 3 # step one tick inside the spread when it is at least this wide
    tick: float = 0.01


@dataclass(frozen=True)
class Child:
    action: str
    qty: int
    price: float
    passive: bool


def ac_kappa(sigma: float, eta: float, risk_aversion: float) -> float:
    """Almgren-Chriss urgency (per tick). 0 if any input is missing -> fall back to front_load."""
    if sigma <= 0 or eta <= 0 or risk_aversion <= 0:
        return 0.0
    return math.sqrt(risk_aversion * sigma * sigma / eta)


def book_eta(book: OrderBook, side: str, width: float = 0.10) -> float:
    """
    Temporary-impact coefficient implied by the visible book. With rho shares of depth
    per $ of price, trading v shares in one tick costs about v / (2 rho) per share,
    i.e. eta * v^2 in total with eta = 1 / (2 rho). 0 if the side is empty.
    """
    touch = book.best_bid if side == "bid" else book.best_ask
    if touch is None:
        return 0.0
    depth = book.depth(side, touch - width if side == "bid" else touch + width)
    return 1.0 / (2.0 * depth / width) if depth > 0 else 0.0


def schedule_position(start: int, target: int, start_tick: int, deadline: int, now: int,
                      front_load: float = 0.0, kappa: float = 0.0) -> int:
    """
    Where the position should be by `now`. With kappa > 0 this is the Almgren-Chriss
    path; otherwise with front_load > 0 the curve is concave (more early), which cuts
    exposure to adverse drift on risky blocks.
    """
    if deadline <= start_tick or now >= deadline:
        return target
    frac = max(0.0, min(1.0, (now - start_tick) / (deadline - start_tick)))
    if kappa > 0:
        span = deadline - start_tick
        kt = min(kappa * span, 50.0)                    # sinh overflows far past "do it all now"
        frac = 1.0 - math.sinh(kt * (1.0 - frac)) / math.sinh(kt)
    elif front_load > 0:
        k = 1 + 4 * front_load
        frac = (1 - math.exp(-k * frac)) / (1 - math.exp(-k))
    return int(round(start + (target - start) * frac))


def plan_children(book: OrderBook, position: int, sched: int, target: int, ticks_left: int,
                  max_order: int, p: AlgoParams, limit_price: float | None = None,
                  passive_only: bool = False) -> list[Child]:
    """
    Pure function: the child orders to have working this loop. `passive_only`: never cross, just
    rest at the touch (the case closes what is left at the last price, so crossing buys nothing).
    """
    remaining = target - position
    if remaining == 0 or book.best_bid is None or book.best_ask is None:
        return []
    action = "BUY" if remaining > 0 else "SELL"
    sign = 1 if remaining > 0 else -1
    remaining = abs(remaining)
    behind = max(0, sign * (sched - position))           # units behind schedule
    if ticks_left <= p.urgent_ticks:
        behind = remaining
    if passive_only:
        behind = 0
    children: list[Child] = []

    # ---- aggressive part: catch up with the schedule
    if behind > 0:
        touch = book.best_ask if action == "BUY" else book.best_bid
        lim = touch + p.max_slippage if action == "BUY" else touch - p.max_slippage
        if limit_price is not None:
            lim = min(lim, limit_price) if action == "BUY" else max(lim, limit_price)
        if ticks_left <= p.urgent_ticks:
            qty = min(behind, max_order)                 # urgent: rely on the protective limit only
        else:
            depth = book.depth("ask" if action == "BUY" else "bid", lim)
            cap = min(behind, max_order, max(1, int(depth * p.participation)))
            qty = book.max_qty_within(action, touch + sign * p.max_slippage / 2, cap)
        if qty > 0:
            children.append(Child(action, qty, lim, passive=False))
            remaining -= qty

    # ---- passive part: rest the rest (iceberg) at the touch to earn the spread
    if remaining > 0 and (ticks_left > p.urgent_ticks or passive_only):
        spread_ticks = round((book.best_ask - book.best_bid) / p.tick)
        if action == "SELL":
            px = book.best_ask - (p.tick if spread_ticks >= p.improve_if_spread_ticks else 0)
            if limit_price is not None:
                px = max(px, limit_price)
        else:
            px = book.best_bid + (p.tick if spread_ticks >= p.improve_if_spread_ticks else 0)
            if limit_price is not None:
                px = min(px, limit_price)
        qty = min(remaining, p.display_qty, max_order)
        children.append(Child(action, qty, round(px, 6), passive=True))
    return children


@dataclass
class Block:
    ticker: str
    start_pos: int
    target: int
    start_tick: int
    deadline: int
    limit_price: float | None = None
    kappa: float = 0.0               # Almgren-Chriss urgency; 0 = use front_load

    def __str__(self) -> str:
        return (f"{self.ticker} {self.start_pos:+d} -> {self.target:+d} by tick {self.deadline}"
                + (f" limit {self.limit_price:.2f}" if self.limit_price is not None else "")
                + (f" AC kappa*T {self.kappa * (self.deadline - self.start_tick):.2f}" if self.kappa else ""))


class BlockExecutor:
    """
    Works any number of blocks (one per ticker). Call `step()` every loop with the
    current positions; aggressive children go out as IOC, passive children are
    kept resting by a QuoteManager and only re-posted when the price moves.
    """

    def __init__(self, executor, params: AlgoParams | None = None):
        from .execution import QuoteManager
        self.ex = executor
        self.p = params or AlgoParams()
        self.qm = QuoteManager(executor, tolerance=self.p.tick / 2, never_larger=True)
        self.blocks: dict[str, Block] = {}
        self.passive_from: int | None = None     # abs tick from which nothing crosses (hold to the bell)
        self.passive: set[str] = set()           # tickers that only rest this loop (risk within budget)

    def work(self, ticker: str, position: int, target: int, now: int, deadline: int,
             limit_price: float | None = None, kappa: float = 0.0) -> Block:
        b = Block(ticker, position, target, now, max(deadline, now + 1), limit_price, kappa)
        self.blocks[ticker] = b
        log.info("BLOCK start %s", b)
        return b

    def cancel(self, ticker: str) -> None:
        self.blocks.pop(ticker, None)
        for side in ("BUY", "SELL"):
            self.qm.sync(ticker, side, None, 0)

    def step(self, books: dict[str, OrderBook], positions: dict[str, int], now: int,
             open_ids: set[int] | None) -> None:
        for t, b in list(self.blocks.items()):
            pos = positions.get(t, 0)
            if pos == b.target:
                log.info("BLOCK done %s", b)
                self.cancel(t)
                continue
            sched = schedule_position(b.start_pos, b.target, b.start_tick, b.deadline, now, self.p.front_load,
                                      b.kappa)
            kids = plan_children(books[t], pos, sched, b.target, b.deadline - now,
                                 self.ex.max_size(t), self.p, b.limit_price,
                                 passive_only=t in self.passive or (self.passive_from is not None
                                                                     and now >= self.passive_from))
            passive = {c.action: c for c in kids if c.passive}
            for c in kids:
                if not c.passive:
                    self.ex.limit(t, c.action, c.qty, c.price, ioc=True)
            for side in ("BUY", "SELL"):
                c = passive.get(side)
                self.qm.sync(t, side, c.price if c else None, c.qty if c else 0, open_ids)

    def step_fresh(self, books: dict[str, OrderBook], positions: dict[str, int], now: int, refresh,
                   target_of) -> None:
        """
        `step` for a slow loop against a live book. Resting orders keep filling while a loop runs, so
        positions read at the start are stale by the time orders go out, and an order sized from them
        can take the position through zero (fined as speculation in the liability case). So:
        1. plan from the loop's positions and cancel every resting quote that will not be kept;
        2. `refresh()` -> (open orders, positions), read in that order: a fill between the two reads is
           counted twice (safe), never missed;
        3. re-plan from the fresh positions and cap IOC + new resting at what is left to unwind after
           the kept quotes' unfilled shares, never past zero.
        """
        kept: dict[tuple[str, str], bool] = {}
        batch = getattr(self.ex, "batched_cancels", None)
        with batch() if batch else nullcontext():   # all stale quotes in one round trip, done before refresh()
            for t, b in self.blocks.items():
                kids = self._kids(t, b, books[t], positions.get(t, 0), now)
                passive = {c.action: c for c in kids if c.passive}
                for side in ("BUY", "SELL"):
                    c = passive.get(side)
                    kept[(t, side)] = self.qm.keeps(t, side, c.price if c else None, c.qty if c else 0)
        orders, fresh = refresh()
        open_ids = {o.get("order_id") for o in orders}
        for t, b in list(self.blocks.items()):
            pos = fresh.get(t, 0)
            # Sent, but not in the OPEN list yet (real RIT is asynchronous): still working, fills maybe unbooked.
            flying = self.ex.in_flight(t, open_ids)
            b.target = target_of(t, pos)
            if pos == b.target or (b.target - pos > 0) == (pos > 0) and pos != 0:
                log.info("BLOCK done %s", b)
                self.cancel(t)
                continue
            side = "BUY" if b.target > pos else "SELL"
            left = min(abs(b.target - pos), abs(pos))      # never through zero
            left -= sum(q for a, q in flying.values() if a == side)
            for o in orders:
                if o.get("ticker") != t:
                    continue
                q = self.qm.live.get((t, str(o.get("action", "")).upper()))
                if q is not None and q.order_id == o.get("order_id"):
                    q.quantity = int(o.get("quantity", 0)) - int(o.get("quantity_filled", 0) or 0)
                if str(o.get("action", "")).upper() == side:
                    left -= int(o.get("quantity", 0)) - int(o.get("quantity_filled", 0) or 0)
            for s_ in ("BUY", "SELL"):
                q = self.qm.live.get((t, s_))
                if q is not None and q.order_id not in open_ids and q.order_id not in flying:
                    self.qm.live.pop((t, s_), None)          # filled or gone: forget it
                    kept[(t, s_)] = False
            # Our orders the quote manager lost track of (e.g. a cancel that hit an order RIT had not booked
            # yet, 404) rest unmanaged at a stale price: cancel them. They still count above for this loop.
            tracked = {q.order_id for q in self.qm.live.values()}
            for o in orders:
                oid = o.get("order_id")
                if o.get("ticker") == t and oid not in tracked and self.ex.ours_settled(oid):
                    self.ex.cancel(oid)
            for c in self._kids(t, b, books[t], pos, now):
                if c.action != side or left <= 0:
                    continue
                if c.passive:
                    if kept.get((t, side)) and (t, side) in self.qm.live:
                        continue                              # its unfilled shares are already counted
                    qty = min(c.qty, left)
                    self.qm.sync(t, side, c.price, qty)
                else:
                    qty = min(c.qty, left)
                    self.ex.limit(t, c.action, qty, c.price, ioc=True)
                left -= qty
            other = "SELL" if side == "BUY" else "BUY"
            if (t, other) in self.qm.live:
                self.qm.sync(t, other, None, 0)

    def _kids(self, t: str, b: Block, book: OrderBook, pos: int, now: int) -> list[Child]:
        sched = schedule_position(b.start_pos, b.target, b.start_tick, b.deadline, now, self.p.front_load, b.kappa)
        return plan_children(book, pos, sched, b.target, b.deadline - now, self.ex.max_size(t), self.p,
                             b.limit_price, passive_only=t in self.passive or (self.passive_from is not None
                                                                                and now >= self.passive_from))

    @property
    def active(self) -> bool:
        return bool(self.blocks)
