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
"""

from __future__ import annotations

import logging
import math
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


def schedule_position(start: int, target: int, start_tick: int, deadline: int, now: int,
                      front_load: float = 0.0) -> int:
    """
    Where the position should be by `now`. With front_load > 0 the curve is
    concave (more early), which cuts exposure to adverse drift on risky blocks.
    """
    if deadline <= start_tick or now >= deadline:
        return target
    frac = max(0.0, min(1.0, (now - start_tick) / (deadline - start_tick)))
    if front_load > 0:
        k = 1 + 4 * front_load
        frac = (1 - math.exp(-k * frac)) / (1 - math.exp(-k))
    return int(round(start + (target - start) * frac))


def plan_children(book: OrderBook, position: int, sched: int, target: int, ticks_left: int,
                  max_order: int, p: AlgoParams, limit_price: float | None = None) -> list[Child]:
    """Pure function: the child orders to have working this loop."""
    remaining = target - position
    if remaining == 0 or book.best_bid is None or book.best_ask is None:
        return []
    action = "BUY" if remaining > 0 else "SELL"
    sign = 1 if remaining > 0 else -1
    remaining = abs(remaining)
    behind = max(0, sign * (sched - position))           # units behind schedule
    if ticks_left <= p.urgent_ticks:
        behind = remaining
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
    if remaining > 0 and ticks_left > p.urgent_ticks:
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

    def __str__(self) -> str:
        return (f"{self.ticker} {self.start_pos:+d} -> {self.target:+d} by tick {self.deadline}"
                + (f" limit {self.limit_price:.2f}" if self.limit_price is not None else ""))


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
        self.qm = QuoteManager(executor, tolerance=self.p.tick / 2)
        self.blocks: dict[str, Block] = {}

    def work(self, ticker: str, position: int, target: int, now: int, deadline: int,
             limit_price: float | None = None) -> Block:
        b = Block(ticker, position, target, now, max(deadline, now + 1), limit_price)
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
            sched = schedule_position(b.start_pos, b.target, b.start_tick, b.deadline, now, self.p.front_load)
            kids = plan_children(books[t], pos, sched, b.target, b.deadline - now,
                                 self.ex.max_size(t), self.p, b.limit_price)
            passive = {c.action: c for c in kids if c.passive}
            for c in kids:
                if not c.passive:
                    self.ex.limit(t, c.action, c.qty, c.price, ioc=True)
            for side in ("BUY", "SELL"):
                c = passive.get(side)
                self.qm.sync(t, side, c.price if c else None, c.qty if c else 0, open_ids)

    @property
    def active(self) -> bool:
        return bool(self.blocks)
