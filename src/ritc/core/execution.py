"""
Order execution: every order a strategy wants to send goes through `Executor`.

Centralising this gives us three guarantees for free:

1. **Dry-run is global.** With `dry_run=True` nothing reaches the exchange; the
   decision is logged instead. Every bot defaults to dry-run.
2. **No naked market orders.** Aggressive trades are sent as *marketable limits*
   at a protective price. A market order sized off a stale book can fill
   several levels worse; a crossing limit fills the good part and stops.
3. **Orders are always sliced** to the case's max order size.
4. **Aggressive orders are immediate-or-cancel.** RIT has no IOC order type,
   so any unfilled remainder of an aggressive order is cancelled by `sweep()`
   at the start of the next loop. Without this, a protective limit that did not
   fill keeps resting, the bot sees an unchanged position and sends another one
   every loop - positions balloon far past their targets. Only market-making
   quotes (`ioc=False`) are allowed to rest.
5. **Every fill is measured** against the arrival mid (`tca`), see core/tca.py.
6. **Multi-leg orders go out concurrently** (`limit_many`): the gap between legs
   is leg risk, so ETF/basket and spread trades are sent in parallel.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from .client import RITClient, RITError, slice_qty
from .tca import TCA

log = logging.getLogger("ritc.exec")


@dataclass
class Executor:
    client: RITClient
    dry_run: bool = True
    max_order_size: dict[str, int] = field(default_factory=dict)
    default_max_order: int = 5_000
    decimals: int = 2
    sent: int = 0
    tca: TCA = field(default_factory=TCA)
    arrival: Callable[[str], float | None] | None = None    # set each loop by the Runner (snapshot mid)
    _ioc: list[int] = field(default_factory=list)
    _tracked: dict[int, dict] = field(default_factory=dict)  # order_id -> fill-tracking state
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def max_size(self, ticker: str) -> int:
        return int(self.max_order_size.get(ticker, self.default_max_order))

    def _tag(self) -> str:
        return "[DRY]" if self.dry_run else "[LIVE]"

    def sweep(self) -> int:
        """Cancel the unfilled remainder of every IOC order sent since the last sweep."""
        with self._lock:
            ids, self._ioc = self._ioc, []
        for oid in ids:
            self.cancel(oid)
        return len(ids)

    def limit(self, ticker: str, action: str, quantity: int, price: float, ioc: bool = True) -> list[dict]:
        """
        Send a (possibly marketable) limit order, sliced. Returns API responses.
        `ioc=True` (default): any unfilled remainder is cancelled at the next sweep().
        """
        out: list[dict] = []
        quantity = int(quantity)
        if quantity <= 0:
            return out
        style = "aggressive" if ioc else "passive"
        arrival = self.arrival(ticker) if self.arrival else None
        for chunk in slice_qty(quantity, self.max_size(ticker)):
            px = RITClient.round_limit(price, action, self.decimals)
            log.info("%s %s %s %d @ %.*f", self._tag(), action.upper(), ticker, chunk, self.decimals, px)
            if self.dry_run:
                continue
            try:
                resp = self.client.limit_order(ticker, action, chunk, price, self.decimals)
            except RITError as exc:
                log.warning("order rejected: %s", exc)
                break
            out.append(resp)
            with self._lock:
                self.sent += 1
                self.tca.record_sent(ticker, chunk, style)
            if not isinstance(resp, dict) or resp.get("order_id") is None:
                continue
            oid = int(resp["order_id"])
            filled = int(resp.get("quantity_filled", 0) or 0)
            vwap = resp.get("vwap")
            self.tca.record_fill(ticker, action, filled, vwap, arrival, style)
            if filled < chunk:
                with self._lock:
                    if ioc:
                        self._ioc.append(oid)
                    self._tracked[oid] = {"ticker": ticker, "action": action.upper(), "arrival": arrival,
                                          "style": style, "filled": filled,
                                          "cost": filled * float(vwap) if filled and vwap else 0.0}
        return out

    def limit_many(self, orders: list[tuple[str, str, int, float]], ioc: bool = True) -> list[list[dict]]:
        """Send several (ticker, action, qty, price) orders concurrently - e.g. all legs of an arb."""
        orders = [o for o in orders if int(o[2]) > 0]
        if self.dry_run or len(orders) <= 1:
            return [self.limit(t, a, q, p, ioc) for t, a, q, p in orders]
        res = self.client.parallel([lambda o=o: self.limit(o[0], o[1], o[2], o[3], ioc) for o in orders])
        return [r if isinstance(r, list) else [] for r in res]

    def reconcile(self) -> int:
        """
        Pick up fills that happened AFTER submission (resting quotes, IOC remainders filled
        before the sweep) so TCA sees passive fills too. Call every few loops and at shutdown.
        """
        if self.dry_run or not self._tracked:
            return 0
        n = 0
        try:
            done = self.client.orders("TRANSACTED") + self.client.orders("CANCELLED")
        except RITError:
            return 0
        for o in done:
            oid = int(o.get("order_id", -1))
            st = self._tracked.pop(oid, None)
            if st is None:
                continue
            total = int(o.get("quantity_filled", 0) or 0)
            inc = total - st["filled"]
            vwap = o.get("vwap")
            if inc > 0 and vwap is not None:
                inc_px = (float(vwap) * total - st["cost"]) / inc
                self.tca.record_fill(st["ticker"], st["action"], inc, inc_px, st["arrival"], st["style"])
                n += 1
        return n

    def market(self, ticker: str, action: str, quantity: int) -> list[dict]:
        """Only for emergencies (flattening at the bell). Prefer `limit`."""
        out: list[dict] = []
        for chunk in slice_qty(int(quantity), self.max_size(ticker)):
            log.info("%s MARKET %s %s %d", self._tag(), action.upper(), ticker, chunk)
            if self.dry_run:
                continue
            try:
                out.append(self.client.market_order(ticker, action, chunk))
                self.sent += 1
            except RITError as exc:
                log.warning("market order rejected: %s", exc)
                break
        return out

    def cancel_all(self, ticker: str | None = None) -> None:
        if self.dry_run:
            return
        try:
            self.client.cancel_all(ticker)
        except RITError as exc:
            log.warning("cancel failed: %s", exc)

    def cancel(self, order_id: int) -> None:
        if self.dry_run:
            return
        try:
            self.client.cancel(order_id)
        except RITError as exc:
            log.debug("cancel %s failed (probably already filled): %s", order_id, exc)


@dataclass
class Quote:
    order_id: int | None
    price: float
    quantity: int


class QuoteManager:
    """
    Keeps one resting order per (ticker, side) and only cancel/replaces when the
    target price moves by at least `tolerance` or size changes materially.
    Every cancel/replace costs queue priority and API budget, so don't churn.
    """

    def __init__(self, executor: Executor, tolerance: float = 0.01):
        self.ex = executor
        self.tolerance = tolerance
        self.live: dict[tuple[str, str], Quote] = {}

    def sync(self, ticker: str, side: str, price: float | None, quantity: int,
             open_ids: set[int] | None = None) -> None:
        key = (ticker, side.upper())
        cur = self.live.get(key)
        # Our order was filled or cancelled by the exchange: forget it.
        if cur and cur.order_id is not None and open_ids is not None and cur.order_id not in open_ids:
            cur = None
            self.live.pop(key, None)
        if price is None or quantity <= 0:
            if cur:
                if cur.order_id is not None:
                    self.ex.cancel(cur.order_id)
                self.live.pop(key, None)
            return
        if cur and abs(cur.price - price) < self.tolerance and abs(cur.quantity - quantity) < max(1, quantity // 4):
            return
        if cur and cur.order_id is not None:
            self.ex.cancel(cur.order_id)
        resp = self.ex.limit(ticker, side, quantity, price, ioc=False)
        oid = resp[0].get("order_id") if resp and isinstance(resp[0], dict) else None
        self.live[key] = Quote(oid, price, quantity)

    def clear(self) -> None:
        for q in self.live.values():
            if q.order_id is not None:
                self.ex.cancel(q.order_id)
        self.live.clear()
