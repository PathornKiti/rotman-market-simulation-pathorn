"""
ETF TRADING - arbitrage between an ETF and its underlying basket.

The edge
--------
An ETF is worth its basket (NAV). When the ETF trades away from NAV by more
than the round-trip costs, we buy the cheap side and sell the rich side:

    premium (rich) = ETF_bid  - NAV_ask  - costs   > entry_edge  ->  SELL ETF, BUY basket
    discount(cheap)= NAV_bid  - ETF_ask  - costs   > entry_edge  ->  BUY ETF,  SELL basket

The position is hedged from the moment both legs fill, so P&L is locked in up to
convergence. We take it off when the gap closes back inside `exit_edge`
(capturing the convergence a second time), or redeem/create through the
converter if the case offers one. Optional FX: if the ETF trades in another
currency, NAV is converted with the FX mid.

Sizing walks every leg's book, so the edge we act on is the edge we actually get.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.book import OrderBook
from ..core.bot import Snapshot, Strategy

log = logging.getLogger("ritc.etf")


@dataclass(frozen=True)
class ArbPlan:
    direction: str                # "SELL_ETF" (ETF rich) or "BUY_ETF" (ETF cheap)
    etf_qty: int
    edge_per_unit: float          # after fees and slippage, per ETF unit
    etf_px: float                 # protective limit for the ETF leg
    leg_px: dict[str, float]      # protective limit per component


def nav(prices: dict[str, float], weights: dict[str, float], fx: float = 1.0) -> float:
    return fx * sum(w * prices[t] for t, w in weights.items())


def _vwap_or_none(book: OrderBook, action: str, qty: int) -> tuple[float, float] | None:
    f = book.walk(action, qty)
    return (f.vwap, f.worst) if f.complete and qty > 0 else None


def plan_arb(etf_book: OrderBook, comp_books: dict[str, OrderBook], weights: dict[str, float],
             fees: dict[str, float], etf: str, entry_edge: float, max_qty: int,
             lot: int = 100, fx: float = 1.0, converter_cost: float = 0.0) -> ArbPlan | None:
    """
    Largest ETF quantity (multiple of `lot`) for which the arbitrage still clears
    `entry_edge` per unit after walking every book. None if no trade.
    """
    fee_unit = fees.get(etf, 0.0) + fx * sum(w * fees.get(t, 0.0) for t, w in weights.items()) + converter_cost

    def evaluate(direction: str, q: int) -> ArbPlan | None:
        etf_side = "SELL" if direction == "SELL_ETF" else "BUY"
        comp_side = "BUY" if direction == "SELL_ETF" else "SELL"
        e = _vwap_or_none(etf_book, etf_side, q)
        if e is None:
            return None
        legs, leg_px = {}, {}
        for t, w in weights.items():
            r = _vwap_or_none(comp_books[t], comp_side, max(1, int(round(q * w))))
            if r is None:
                return None
            legs[t], leg_px[t] = r
        basket = nav(legs, weights, fx)
        edge = (e[0] - basket if direction == "SELL_ETF" else basket - e[0]) - fee_unit
        return ArbPlan(direction, q, edge, e[1], leg_px)

    best: ArbPlan | None = None
    for direction in ("SELL_ETF", "BUY_ETF"):
        first = evaluate(direction, lot)
        if first is None or first.edge_per_unit < entry_edge:
            continue
        # Edge only shrinks with size (we walk deeper), so binary search the max size.
        lo, hi, found = 1, max(1, max_qty // lot), first
        while lo <= hi:
            mid = (lo + hi) // 2
            p = evaluate(direction, mid * lot)
            if p is not None and p.edge_per_unit >= entry_edge:
                found, lo = p, mid + 1
            else:
                hi = mid - 1
        if best is None or found.edge_per_unit * found.etf_qty > best.edge_per_unit * best.etf_qty:
            best = found
    return best


def hedge_residuals(positions: dict[str, int], etf: str, weights: dict[str, float],
                    tolerance: int = 100) -> dict[str, int]:
    """Signed component trades needed so the basket exactly offsets the ETF position."""
    out = {}
    etf_pos = positions.get(etf, 0)
    for t, w in weights.items():
        target = -int(round(etf_pos * w))
        diff = target - positions.get(t, 0)
        if abs(diff) >= tolerance:
            out[t] = diff
    return out


def exit_signal(etf_pos: int, premium_mid: float, exit_edge: float) -> bool:
    """
    Close once the mispricing we are positioned for has converged.
    Short ETF (bought when rich) exits when premium <= exit_edge, and vice versa.
    """
    if etf_pos < 0:
        return premium_mid <= exit_edge
    if etf_pos > 0:
        return premium_mid >= -exit_edge
    return False


class ETFStrategy(Strategy):
    name = "etf"

    def __init__(self, ctx):
        super().__init__(ctx)
        c, s = self.cfg.get("case", {}), self.cfg.get("strategy", {})
        self.etf: str = c["etf"]
        self.weights: dict[str, float] = {k: float(v) for k, v in c["components"].items()}
        self.fees: dict[str, float] = {k: float(v) for k, v in c.get("fee", {}).items()}
        self.fx_ticker: str = c.get("fx_ticker", "")
        self.fx_mode: str = c.get("fx_mode", "multiply")
        self.converter_cost = float(c.get("converter_cost", 0.0))
        self.p = s

    @property
    def book_tickers(self) -> list[str]:
        return [self.etf, *self.weights]

    def fx(self, snap: Snapshot) -> float:
        if not self.fx_ticker:
            return 1.0
        m = snap.mid(self.fx_ticker) or 1.0
        return m if self.fx_mode == "multiply" else 1.0 / m

    def step(self, snap: Snapshot) -> None:
        positions = snap.positions
        fx = self.fx(snap)

        # 1) Repair any leg imbalance from a partial fill before doing anything new.
        for t, diff in hedge_residuals(positions, self.etf, self.weights, self.p.get("hedge_tolerance", 100)).items():
            bid, ask = snap.quote(t)
            action = "BUY" if diff > 0 else "SELL"
            px = (ask or 0) + 0.05 if action == "BUY" else (bid or 0) - 0.05
            log.info("REHEDGE %s %d %s", action, abs(diff), t)
            self.ex.limit(t, action, abs(diff), px)
            positions[t] = positions.get(t, 0) + diff

        etf_book = snap.book(self.etf)
        comp_books = {t: snap.book(t) for t in self.weights}
        mids = {t: b.mid for t, b in comp_books.items()}
        if etf_book.mid is None or any(v is None for v in mids.values()):
            return
        premium = etf_book.mid - nav(mids, self.weights, fx)

        # 2) Take profit on convergence.
        etf_pos = positions.get(self.etf, 0)
        if etf_pos and exit_signal(etf_pos, premium, self.p.get("exit_edge", 0.02)):
            q = min(abs(etf_pos), int(self.p.get("clip", 5000)))
            action = "BUY" if etf_pos < 0 else "SELL"
            log.info("CONVERGED premium %.3f -> close %d", premium, q)
            self._send_legs(action, q, etf_book, comp_books, slip=self.p.get("exit_slippage", 0.03))
            return

        # 3) Open new arbitrage.
        room_sell = self.risk.room(self.etf, "SELL", positions)
        room_buy = self.risk.room(self.etf, "BUY", positions)
        cap = min(int(self.p.get("clip", 5000)), max(room_sell, room_buy))
        if cap < self.p.get("lot", 100):
            return
        plan = plan_arb(etf_book, comp_books, self.weights, self.fees, self.etf,
                        self.p.get("entry_edge", 0.10), cap, int(self.p.get("lot", 100)),
                        fx, self.converter_cost)
        if plan is None:
            return
        room = room_sell if plan.direction == "SELL_ETF" else room_buy
        qty = min(plan.etf_qty, room)
        if qty <= 0:
            return
        log.info("ARB %s %d  edge/unit %.4f  premium %.3f", plan.direction, qty, plan.edge_per_unit, premium)
        etf_action = "SELL" if plan.direction == "SELL_ETF" else "BUY"
        comp_action = "BUY" if etf_action == "SELL" else "SELL"
        # All legs concurrently - sequential legs leave the arb half-done while the book moves.
        self.ex.limit_many([(self.etf, etf_action, qty, plan.etf_px)] +
                           [(t, comp_action, int(round(qty * w)), plan.leg_px[t]) for t, w in self.weights.items()])

    def _send_legs(self, etf_action: str, qty: int, etf_book: OrderBook,
                   comp_books: dict[str, OrderBook], slip: float) -> None:
        comp_action = "BUY" if etf_action == "SELL" else "SELL"
        touch = etf_book.best_ask if etf_action == "BUY" else etf_book.best_bid
        if touch is None:
            return
        orders = [(self.etf, etf_action, qty, touch + slip if etf_action == "BUY" else touch - slip)]
        for t, w in self.weights.items():
            b = comp_books[t]
            tp = b.best_ask if comp_action == "BUY" else b.best_bid
            if tp is not None:
                orders.append((t, comp_action, int(round(qty * w)), tp + slip if comp_action == "BUY" else tp - slip))
        self.ex.limit_many(orders)
