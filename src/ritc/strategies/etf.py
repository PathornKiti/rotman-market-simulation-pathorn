"""
ETF TRADING - arbitrage between an ETF and its underlying basket.

The edge
--------
An ETF is worth its basket (NAV). When the ETF trades away from NAV by more
than the round-trip costs, we buy the cheap side and sell the rich side:

    premium (rich) = ETF_bid  - NAV_ask  - costs   > entry_edge  ->  SELL ETF, BUY basket
    discount(cheap)= NAV_bid  - ETF_ask  - costs   > entry_edge  ->  BUY ETF,  SELL basket

The position is hedged from the moment both legs fill, so P&L is locked in up to
convergence. Optional FX: if the ETF trades in another currency, NAV is converted
with the FX mid.

Exit (`exit_mode`)
------------------
* "executable" (default): closing is just the arb in the other direction, so we
  close when THAT trade's executable edge after fees and slippage clears
  `exit_edge`, sized by walking the books. Round trip >= entry_edge + exit_edge.
* "mid": the original rule - close when the MID premium is back inside
  `exit_edge`. That pays a second full set of spreads and fees just as the gap
  hits zero, and on the simulator it cost more than the arb earned (mean NLV
  went from about -$0.5k to positive when we switched; see docs/ARCHITECTURE.md).
  A position that never reaches its exit edge stays hedged to the end.

Execution
---------
* Sizing walks every leg's book, so the edge we act on is the edge we actually get.
* Risk room is checked for the whole PACKAGE (ETF + every component), because
  all legs use the same gross limit.
* Part of the surplus edge above the threshold (`slippage_share`) is spent on
  wider protective limits, so the legs complete even if the book ticks between
  the snapshot and the order.
* Leg repair: if the basket filled but the ETF did not, we COMPLETE the arb by
  trading the ETF while the premium still favours it. Otherwise we unwind the
  odd component legs (the original behaviour).

Passive ETF leg (`maker = true`)
--------------------------------
Rest a bid and an ask on the ETF at the price where, if filled, hedging with the
basket (walked at `maker_size`) still clears `maker_edge` after the basket's fees,
counting the ETF's limit-order rebate instead of its taker fee. A fill shows up as
a leg imbalance and leg repair buys/sells the basket. The quote never crosses.

Tender offers (`tenders = true`)
--------------------------------
The official Algo case also sends private tender offers on the ETF. A tender is
the ETF leg of an arb at a fixed price with no commission: we accept when hedging
it with the basket (walked on the book, scaled for refill) still clears
`tender_min_edge` per unit and the whole package fits the risk room. Leg repair
then trades the hedge. The hedged position needs no exit: the ETF closes at fair
value (the basket converted), so the edge is locked in at acceptance.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from ..core.book import OrderBook
from ..core.bot import Snapshot, Strategy
from ..core.execution import QuoteManager
from ..pricing.timeseries import bertram_band, fit_ou, ou_continuous
from .liability import scaled_book

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
             lot: int = 100, fx: float = 1.0, converter_cost: float = 0.0,
             directions: tuple[str, ...] = ("SELL_ETF", "BUY_ETF")) -> ArbPlan | None:
    """
    Largest ETF quantity (multiple of `lot`) for which the arbitrage still clears
    `entry_edge` per unit after walking every book. None if no trade.
    `directions` restricts the search, e.g. to the side that closes a position.
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
    for direction in directions:
        first = evaluate(direction, min(lot, max_qty))
        if first is None or first.edge_per_unit < entry_edge:
            continue
        # Edge only shrinks with size (we walk deeper), so binary search the max size.
        lo, hi, found = 1, max_qty // lot, first
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


def tender_edge(tender: dict, comp_books: dict[str, OrderBook], weights: dict[str, float],
                fees: dict[str, float], fx: float = 1.0, refill_factor: float = 2.0) -> float | None:
    """
    Edge per ETF unit (ETF currency) of accepting a fixed-price ETF tender and hedging it
    with the basket: BUY the ETF at the tender price and SELL the basket, or the reverse.
    The basket is walked on books scaled by `refill_factor` (the hedge can take a few ticks).
    The tender pays no commission; the basket legs pay their taker fees. None if unpriceable.
    """
    qty, px = int(tender.get("quantity", 0)), tender.get("price")
    if qty <= 0 or px is None:
        return None
    buy = str(tender.get("action", "BUY")).upper() == "BUY"
    legs = {}
    for t, w in weights.items():
        n = max(1, int(round(qty * w)))
        f = scaled_book(comp_books[t], refill_factor).walk("SELL" if buy else "BUY", n)
        if not f.filled:
            return None
        # Anything the scaled book can't absorb is priced a nickel past its worst level.
        rest = n - f.filled
        legs[t] = (f.vwap * f.filled + (f.worst + (-0.05 if buy else 0.05)) * rest) / n
    basket = nav(legs, weights, fx)
    fee = fx * sum(w * fees.get(t, 0.0) for t, w in weights.items())
    return (basket - float(px) if buy else float(px) - basket) - fee


def maker_quotes(comp_books: dict[str, OrderBook], weights: dict[str, float], fees: dict[str, float],
                 qty: int, edge: float, rebate: float, fx: float = 1.0,
                 tick: float = 0.01) -> tuple[float | None, float | None]:
    """
    (bid, ask) for a resting ETF order of `qty` that, once filled, can be hedged with the
    basket at the current books for at least `edge` per unit: ask = basket ask + basket
    fees - rebate + edge, bid = basket bid - basket fees + rebate - edge. None for a side
    whose basket book is too thin.
    """
    fee = fx * sum(w * fees.get(t, 0.0) for t, w in weights.items())
    out = []
    for side in ("SELL", "BUY"):                 # hedge side: SELL basket backs our ETF bid
        legs = {}
        for t, w in weights.items():
            r = _vwap_or_none(comp_books[t], side, max(1, int(round(qty * w))))
            if r is None:
                break
            legs[t] = r[0]
        else:
            basket = nav(legs, weights, fx)
            out.append(basket - fee + rebate - edge if side == "SELL" else basket + fee - rebate + edge)
            continue
        out.append(None)
    bid, ask = out
    return (None if bid is None else math.floor(bid / tick + 1e-9) * tick,
            None if ask is None else math.ceil(ask / tick - 1e-9) * tick)


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


def with_slippage(plan: ArbPlan, weights: dict[str, float], entry_edge: float, share: float,
                  fx: float = 1.0) -> ArbPlan:
    """
    Widen each leg's protective limit by an equal part of `share` x (edge - entry_edge),
    so the trade still clears `entry_edge` if every leg fills at its widened limit.
    """
    budget = max(0.0, plan.edge_per_unit - entry_edge) * max(0.0, min(1.0, share))
    if budget <= 0:
        return plan
    per_leg = budget / (1 + len(weights))                 # $ per ETF unit, per leg
    etf_sign = -1 if plan.direction == "SELL_ETF" else 1  # SELL: lower limit, BUY: higher
    leg_px = {t: plan.leg_px[t] - etf_sign * per_leg / max(w * fx, 1e-9) for t, w in weights.items()}
    return ArbPlan(plan.direction, plan.etf_qty, plan.edge_per_unit,
                   plan.etf_px + etf_sign * per_leg, leg_px)


def repair_legs(positions: dict[str, int], etf: str, weights: dict[str, float], premium: float,
                tolerance: int = 100) -> dict[str, int]:
    """
    Signed trades that make the book a hedged arb again after a partial fill.
    If every component agrees on the ETF position it hedges (the ETF leg is the one
    that missed) and the premium still favours that ETF trade, complete the arb on
    the ETF. Otherwise square the components to the ETF (`hedge_residuals`).
    """
    implied = [-positions.get(t, 0) / w for t, w in weights.items() if w]
    if implied and max(implied) - min(implied) < tolerance:
        diff = int(round(sum(implied) / len(implied))) - positions.get(etf, 0)
        if abs(diff) >= tolerance and (premium > 0 if diff < 0 else premium < 0):
            return {etf: diff}
    return hedge_residuals(positions, etf, weights, tolerance)


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
        # Bertram thresholds: premium history (one point per tick), refit periodically.
        self.prem_hist: list[float] = []
        self.last_tick = -1
        self.band: float | None = None          # Bertram a* on the mid premium, once fitted
        self.seen: set[int] = set()             # tender ids already decided
        self.wants_tenders = bool(s.get("tenders", True))
        self.maker = bool(s.get("maker", False))
        self.qm = QuoteManager(self.ex, tolerance=0.01)

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
        etf_book = snap.book(self.etf)
        comp_books = {t: snap.book(t) for t in self.weights}
        mids = {t: b.mid for t, b in comp_books.items()}
        if etf_book.mid is None or any(v is None for v in mids.values()):
            return
        premium = etf_book.mid - nav(mids, self.weights, fx)
        lot = int(self.p.get("lot", 100))
        entry, exit_edge = self.thresholds(snap, premium, etf_book, comp_books, fx)
        if self.wants_tenders:
            self.handle_tenders(snap, comp_books, fx)
        self.hedge_fx(positions)

        # 1) Repair any leg imbalance from a partial fill before doing anything new.
        repairs = repair_legs(positions, self.etf, self.weights, premium, self.p.get("hedge_tolerance", 100))
        for t, diff in repairs.items():
            bid, ask = snap.quote(t)
            action = "BUY" if diff > 0 else "SELL"
            px = (ask or 0) + 0.05 if action == "BUY" else (bid or 0) - 0.05
            log.info("REPAIR %s %d %s (premium %.3f)", action, abs(diff), t, premium)
            got = self.ex.filled(self.ex.limit(t, action, abs(diff), px), abs(diff))
            positions[t] = positions.get(t, 0) + (got if diff > 0 else -got)
        if repairs:
            self._quote_etf(None, None, 0, 0)            # don't add to an unhedged book
            return
        if self.maker:
            self._maker_step(snap, etf_book, comp_books, fx, positions)

        # 2) Take profit.
        etf_pos = positions.get(self.etf, 0)
        if etf_pos and self.p.get("exit_mode", "executable") == "mid":
            if exit_signal(etf_pos, premium, self.p.get("exit_edge", 0.02)):
                q = min(abs(etf_pos), int(self.p.get("clip", 5000)))
                action = "BUY" if etf_pos < 0 else "SELL"
                log.info("CONVERGED premium %.3f -> close %d", premium, q)
                self._send_legs(action, q, etf_book, comp_books, slip=self.p.get("exit_slippage", 0.03))
                return
        elif etf_pos:
            close_dir = "BUY_ETF" if etf_pos < 0 else "SELL_ETF"
            plan = plan_arb(etf_book, comp_books, self.weights, self.fees, self.etf, exit_edge,
                            min(abs(etf_pos), int(self.p.get("clip", 5000))), lot, fx, self.converter_cost,
                            directions=(close_dir,))
            if plan is not None:
                log.info("CLOSE %s %d  edge/unit %.4f  premium %.3f", plan.direction, plan.etf_qty,
                         plan.edge_per_unit, premium)
                self._execute(with_slippage(plan, self.weights, exit_edge,
                                            self.p.get("slippage_share", 0.5), fx), plan.etf_qty)
                return

        # 3) Open new arbitrage, sized against every limit for the WHOLE package.
        room = {d: self.risk.room_package(self._package(d), positions) for d in ("SELL_ETF", "BUY_ETF")}
        cap = min(self.sized(self.p.get("clip", 5000)), max(room.values()))
        if cap < lot:
            return
        plan = plan_arb(etf_book, comp_books, self.weights, self.fees, self.etf, entry, cap, lot,
                        fx, self.converter_cost)
        if plan is None:
            return
        qty = min(plan.etf_qty, room[plan.direction])
        if qty < lot:
            return
        log.info("ARB %s %d  edge/unit %.4f  premium %.3f", plan.direction, qty, plan.edge_per_unit, premium)
        self._execute(with_slippage(plan, self.weights, entry, self.p.get("slippage_share", 0.5), fx), qty)

    def _maker_step(self, snap: Snapshot, etf_book: OrderBook, comp_books: dict[str, OrderBook], fx: float,
                    positions: dict[str, int]) -> None:
        size = int(self.p.get("maker_size", 2000))
        bid, ask = maker_quotes(comp_books, self.weights, self.fees, size, self.p.get("maker_edge", 0.02),
                                self.p.get("maker_rebate", 0.01), fx)
        if etf_book.best_bid is None or etf_book.best_ask is None:
            bid = ask = None
        else:                                         # stay passive: never cross the ETF touch
            bid = None if bid is None else min(bid, round(etf_book.best_ask - 0.01, 2))
            ask = None if ask is None else max(ask, round(etf_book.best_bid + 0.01, 2))
        room_buy = self.risk.room_package(self._package("BUY_ETF"), positions)
        room_sell = self.risk.room_package(self._package("SELL_ETF"), positions)
        self._quote_etf(bid, ask, self.sized(min(size, room_buy)), self.sized(min(size, room_sell)))

    def _quote_etf(self, bid: float | None, ask: float | None, bid_qty: int, ask_qty: int) -> None:
        if not self.maker:
            return
        open_ids = None if self.ex.dry_run else {int(o["order_id"]) for o in self.client.orders("OPEN")}
        lot = int(self.p.get("lot", 100))
        self.qm.sync(self.etf, "BUY", bid, bid_qty // lot * lot, open_ids)
        self.qm.sync(self.etf, "SELL", ask, ask_qty // lot * lot, open_ids)

    def wind_down(self, snap: Snapshot) -> None:
        self.qm.clear()
        self.ex.cancel_all()

    def hedge_fx(self, positions: dict[str, int]) -> None:
        """
        Trade the foreign-currency balance back to flat once it passes `fx_hedge_band`.
        The ETF closes at the basket's value, so in CAD a hedged arb's ETF leg is a CAD asset
        and the USD it was paid with is a naked FX position (sim: ~$7k/heat of noise).
        Market orders: the executor rounds limits to 2 decimals, too coarse for a 4-decimal FX quote.
        The cost is the FX half-spread on the arb's USD turnover (sim: ~$1.3k/heat).
        """
        band = float(self.p.get("fx_hedge_band", 0.0))
        bal = positions.get(self.fx_ticker, 0) if self.fx_ticker else 0
        if band <= 0 or abs(bal) < band:
            return
        qty = min(int(abs(bal)), self.ex.max_size(self.fx_ticker))
        action = "BUY" if bal < 0 else "SELL"
        log.info("FX HEDGE %s %d %s (balance %+d)", action, qty, self.fx_ticker, bal)
        if self.ex.dry_run:
            return
        try:
            self.client.market_order(self.fx_ticker, action, qty)
        except Exception as exc:          # noqa: BLE001 - a failed hedge retries next loop
            log.warning("FX hedge failed: %s", exc)
            return
        positions[self.fx_ticker] = bal + (qty if action == "BUY" else -qty)

    def handle_tenders(self, snap: Snapshot, comp_books: dict[str, OrderBook], fx: float) -> None:
        positions = snap.positions
        for t in self.current_tenders():
            tid = int(t.get("tender_id", -1))
            if tid in self.seen or t.get("ticker") != self.etf:
                continue
            self.seen.add(tid)
            buy = str(t.get("action", "BUY")).upper() == "BUY"
            qty = int(t.get("quantity", 0))
            edge = tender_edge(t, comp_books, self.weights, self.fees, fx, self.p.get("tender_refill", 2.0))
            room = self.risk.room_package(self._package("BUY_ETF" if buy else "SELL_ETF"), positions)
            need = self.p.get("tender_min_edge", 0.02) * self.edge_mult()
            ok = (edge is not None and edge >= need and qty <= room and t.get("is_fixed_bid", True)
                  and snap.ticks_left > int(self.p.get("tender_min_ticks", 10)))
            log.info("TENDER %s %s %d %s @ %s: edge/unit %s, room %d -> %s", tid, t.get("action"), qty, self.etf,
                     t.get("price"), "n/a" if edge is None else f"{edge:+.4f}", room, "ACCEPT" if ok else "decline")
            if self.ex.dry_run:
                continue
            try:
                if ok:
                    resp = self.client.accept_tender(tid)
                    if isinstance(resp, dict) and resp.get("success") is False:
                        continue
                    positions[self.etf] = positions.get(self.etf, 0) + (qty if buy else -qty)
                else:
                    self.client.decline_tender(tid)
            except Exception as exc:       # it may have expired between read and accept
                log.warning("tender %s action failed: %s", tid, exc)

    def cost_per_side(self, etf_book: OrderBook, comp_books: dict[str, OrderBook], fx: float) -> float:
        """Fees + half-spreads of every leg, per ETF unit: what one side of the arb costs."""
        half = lambda b: (b.spread or 0.0) / 2      # noqa: E731
        return (self.fees.get(self.etf, 0.0) + half(etf_book) + self.converter_cost
                + fx * sum(w * (self.fees.get(t, 0.0) + half(comp_books[t])) for t, w in self.weights.items()))

    def thresholds(self, snap: Snapshot, premium: float, etf_book: OrderBook,
                   comp_books: dict[str, OrderBook], fx: float) -> tuple[float, float]:
        """
        (entry_edge, exit_edge) in executable $ per unit. "fixed": from config.
        "bertram": fit an OU to the mid premium and use Bertram's optimal band a*:
        trade when |premium| reaches a*, i.e. an executable edge of a* - cost per side,
        for both entering and for the reverse trade that closes.
        """
        entry = self.p.get("entry_edge", 0.10)
        exit_edge = self.p.get("exit_edge", 0.01)
        if self.p.get("threshold_mode", "bertram") != "bertram":
            return entry, exit_edge
        if snap.tick != self.last_tick:
            self.last_tick = snap.tick
            self.prem_hist.append(premium)
            self.prem_hist = self.prem_hist[-int(self.p.get("bertram_window", 300)):]
            if len(self.prem_hist) >= 60 and len(self.prem_hist) % 10 == 0:
                fit = fit_ou(self.prem_hist)
                if fit is not None and fit.significant():
                    side = self.cost_per_side(etf_book, comp_books, fx)
                    a, _ = bertram_band(*ou_continuous(fit), cost=2 * side)
                    self.band = a - side if a > 0 else None
                    log.info("BERTRAM half-life %.1f ticks, sd %.3f, cost/side %.3f -> edge %.3f",
                             fit.half_life, fit.stationary_std, side, self.band or 0.0)
        if self.band is None:
            return entry, exit_edge                  # not fitted yet / not mean-reverting: config values
        floor = self.p.get("bertram_min_edge", 0.0)
        return max(self.band, floor), max(self.band, floor)

    def _package(self, direction: str) -> dict[str, float]:
        """Signed units of every leg per ETF unit traded in `direction`."""
        s = -1 if direction == "SELL_ETF" else 1
        return {self.etf: s, **{t: -s * w for t, w in self.weights.items()}}

    def _execute(self, plan: ArbPlan, qty: int) -> None:
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
