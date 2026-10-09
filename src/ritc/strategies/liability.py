"""
LIABILITY TRADING - evaluate institutional tender offers, accept the profitable
ones, then unwind the resulting block into the market without moving it.

The edge
--------
A tender is a block trade offered to you at a fixed price (or one you bid).
It is only worth taking if you can get OUT of the block for more than you paid,
after commissions and after the slippage of walking the book. The visible book
understates how much you can unwind (it refills over the unwind horizon), but
it also hides the fact that you push the price against yourself. So:

    expected unwind VWAP = walk( book scaled by refill_factor, quantity )
    profit / share       = (unwind VWAP - tender price) * sign - fees - drift penalty - risk premium
    risk premium         = risk_aversion * GARCH price vol/tick * sqrt(unwind ticks)

The risk premium is the time-series part: holding a block while you unwind it
exposes you to sigma * sqrt(time). GARCH tracks sigma tick by tick, so the same
tender needs a fatter edge when the market has just turned volatile.

Accept iff profit/share >= min_profit AND the block fits inside every risk limit
AND there is enough time left in the period to unwind it.

For competitive (non-fixed) tenders we bid the price that leaves exactly our
required margin - the most aggressive price that is still profitable.

Unwind (block execution)
------------------------
`unwind_mode = "block"` (default) works each accepted block with the
passive-then-aggressive BlockExecutor (core/algo.py): a schedule to flat within
`unwind_horizon_ticks`, resting an iceberg at the touch to EARN the spread
while on schedule and crossing only to catch up. A new tender on the same
ticker restarts the schedule from the new position.

`unwind_mode = "slice"` is the original purely aggressive unwind: each loop take
the largest slice whose VWAP stays within `max_slippage` of the touch, capped by
a participation rate. Kept for A/B testing (`python -m ritc tune`).

Tenders arrive through the real-time feed (polled every ~100 ms), so a
profitable block is evaluated and accepted as soon as it appears.

Crowded tenders (`crowd_learn = true`)
--------------------------------------
In a competition every desk gets the same block and unwinds it into the same
book at the same time, so the price runs against the unwind. The bot learns that
move online from the tenders it does NOT take (`crowd_untaken_only`: our own
unwind's impact is already priced by walking the book): `crowd_ticks` after a
tender appears it scores how far the mid moved against the unwind, per 10k
shares, in a conjugate normal model. Once the crowd is significant (posterior
mean > 2 sd, `crowd_gate`) it charges

    crowd cost/share = crowd_weight * learned $/10k * tender size / 10k

in `evaluate_tender`, and the unwind races it: the schedule shortens to
`crowd_horizon_ticks`. With no crowd the gate never opens and nothing changes
(docs/HOSTILE_MARKET.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.algo import AlgoParams, BlockExecutor, ac_kappa, book_eta
from ..core.book import Level, OrderBook
from ..core.bot import Snapshot, Strategy
from ..pricing.stats import EWMA
from ..pricing.timeseries import OnlineGarch
from .commodity import BayesImpact

log = logging.getLogger("ritc.liability")


@dataclass(frozen=True)
class TenderDecision:
    accept: bool
    price: float | None           # price to submit (competitive tenders) or tender price
    profit_per_share: float
    unwind_vwap: float
    reason: str


def scaled_book(book: OrderBook, factor: float) -> OrderBook:
    """Book with every level's size multiplied - models refill over the unwind horizon."""
    def scale(levels: list[Level]) -> list[Level]:
        return [Level(lv.price, max(1, int(lv.quantity * factor)), lv.trader_id) for lv in levels]
    return OrderBook(book.ticker, scale(book.bids), scale(book.asks))


def evaluate_tender(
    tender: dict,
    book: OrderBook,
    fee: float,
    position: int = 0,
    room: int = 10**9,
    ticks_left: int = 10**9,
    refill_factor: float = 2.0,
    min_profit: float = 0.02,
    competitive_margin: float = 0.05,
    drift_per_tick: float = 0.0,
    unwind_ticks_per_lot: float = 0.0,
    min_ticks_to_unwind: int = 10,
    price_vol_per_tick: float = 0.0,
    risk_aversion: float = 0.0,
    crowd_cost: float = 0.0,
) -> TenderDecision:
    """
    Pure decision function. `tender['action']` is OUR side of the trade:
    BUY means we buy the block from the client (and must later SELL it).
    `drift_per_tick` is the recent mid drift (+ = rising); it is a cost when it
    runs against our unwind direction over the expected unwind time.
    """
    action = str(tender.get("action", "BUY")).upper()
    qty = int(tender.get("quantity", 0))
    tender_px = tender.get("price")
    fixed = bool(tender.get("is_fixed_bid", True))
    sign = 1 if action == "BUY" else -1
    unwind_side = "SELL" if action == "BUY" else "BUY"

    if qty <= 0:
        return TenderDecision(False, None, 0.0, 0.0, "empty tender")
    if qty > room:
        return TenderDecision(False, None, 0.0, 0.0, f"exceeds risk room ({qty} > {room})")
    if ticks_left < min_ticks_to_unwind:
        return TenderDecision(False, None, 0.0, 0.0, f"only {ticks_left} ticks left to unwind")

    # Part of the block may just offset an existing opposite position - that part
    # never has to touch the book and is valued at the touch we would have paid.
    offset = min(qty, max(0, -sign * position))
    to_unwind = qty - offset
    deep = scaled_book(book, refill_factor)

    def unwind_cost(n: int) -> float:
        """Total proceeds/cost of unwinding n units into the refilled book."""
        if n <= 0:
            return 0.0
        fill = deep.walk(unwind_side, n)
        if fill.complete:
            return fill.vwap * n
        # Price the unfilled remainder at the worst visible level, minus a penalty tick.
        worst = fill.worst - sign * 0.05 if fill.filled else (book.mid or 0.0)
        return fill.vwap * fill.filled + worst * (n - fill.filled)

    if to_unwind > 0:
        vwap_unwind = unwind_cost(to_unwind) / to_unwind
    else:
        vwap_unwind = (book.best_bid if action == "BUY" else book.best_ask) or (book.mid or 0.0)

    if offset and to_unwind:
        touch = (book.best_bid if action == "BUY" else book.best_ask) or vwap_unwind
        vwap_unwind = (vwap_unwind * to_unwind + touch * offset) / qty
    elif offset:
        vwap_unwind = (book.best_bid if action == "BUY" else book.best_ask) or vwap_unwind

    if not vwap_unwind:
        return TenderDecision(False, None, 0.0, 0.0, "no market to unwind into")

    # Adverse drift over the time it takes to unwind (only counts against us).
    unwind_ticks = to_unwind * unwind_ticks_per_lot
    drift_cost = max(0.0, -sign * drift_per_tick) * unwind_ticks
    drift_cost += risk_aversion * price_vol_per_tick * unwind_ticks ** 0.5
    drift_cost += crowd_cost             # learned: how far the price runs against tenders' unwinds
    fees = fee * (to_unwind / qty)       # we pay the taker fee only on what hits the book

    if fixed:
        if tender_px is None:
            return TenderDecision(False, None, 0.0, vwap_unwind, "fixed tender without price")
        pps = sign * (vwap_unwind - float(tender_px)) - fees - drift_cost
        ok = pps >= min_profit
        why = f"profit/share {pps:+.4f} {'>=' if ok else '<'} {min_profit:.4f}"
        return TenderDecision(ok, float(tender_px), pps, vwap_unwind, why)

    # Competitive: bid the price that still leaves our margin.
    bid = vwap_unwind - sign * (fees + drift_cost + competitive_margin)
    if tender_px is not None:
        # Some cases show a reference price; never bid worse for ourselves than it.
        ref = float(tender_px)
        bid = min(bid, ref) if action == "BUY" else max(bid, ref)
    pps = sign * (vwap_unwind - bid) - fees - drift_cost
    ok = pps >= min_profit
    return TenderDecision(ok, round(bid, 2), pps, vwap_unwind, f"competitive bid {bid:.2f}, profit/share {pps:+.4f}")


def unwind_slice(book: OrderBook, position: int, max_slippage: float, participation: float,
                 max_order: int) -> tuple[str, int, float] | None:
    """
    Next unwind order: (action, quantity, protective limit price) or None.
    Size = largest qty whose VWAP stays within `max_slippage` of the touch,
    capped at `participation` x visible depth and the max order size.
    """
    if position == 0:
        return None
    action = "SELL" if position > 0 else "BUY"
    touch = book.best_bid if action == "SELL" else book.best_ask
    if touch is None:
        return None
    limit_vwap = touch - max_slippage if action == "SELL" else touch + max_slippage
    visible = book.depth("bid" if action == "SELL" else "ask", limit_vwap)
    cap = min(abs(position), max_order, max(1, int(visible * participation)))
    qty = book.max_qty_within(action, limit_vwap, cap)
    if qty <= 0:
        return None
    worst_px = touch - 2 * max_slippage if action == "SELL" else touch + 2 * max_slippage
    return action, qty, worst_px


class LiabilityStrategy(Strategy):
    name = "liability"
    wants_tenders = True           # tenders via the real-time feed

    def __init__(self, ctx):
        super().__init__(ctx)
        c, s = self.cfg.get("case", {}), self.cfg.get("strategy", {})
        self.tickers: list[str] = list(c.get("tickers", []))
        self.fees: dict[str, float] = c.get("fee", {})
        self.p = s
        self.drift: dict[str, EWMA] = {t: EWMA(s.get("drift_halflife", 15)) for t in self.tickers}
        self.last_mid: dict[str, float] = {}
        self.garch = {t: OnlineGarch() for t in self.tickers}
        self.last_tick = -1
        self.seen: set[int] = set()
        ex_cfg = self.cfg.get("execution", {})
        self.mode = ex_cfg.get("unwind_mode", "block")
        self.horizon = int(ex_cfg.get("unwind_horizon_ticks", 30))
        self.schedule = ex_cfg.get("schedule", "almgren_chriss")          # or "almgren_chriss"
        self.ac_lambda = float(ex_cfg.get("ac_risk_aversion", 3e-7))
        self.algo = BlockExecutor(self.ex, AlgoParams(**{k: v for k, v in ex_cfg.items()
                                                         if k in AlgoParams.__dataclass_fields__}))
        # CROWDED TENDERS: other desks get the same block and unwind it into the same book.
        # Learn, from every tender we see (accepted or not), how far the mid runs against its
        # unwind direction over the next `crowd_ticks`, per 10k shares of tender: a conjugate
        # normal update (BayesImpact) from the prior `crowd_prior_mean`.
        self.crowd_learn = bool(s.get("crowd_learn", False))
        self.crowd = BayesImpact(float(s.get("crowd_prior_mean", 0.0)), float(s.get("crowd_prior_sd", 0.10)),
                                 float(s.get("crowd_noise_sd", 0.10)))
        self.crowd_obs: list[tuple[int, str, int, float, float, int]] = []  # (tick, ticker, sign, x, mid, id)
        self.taken: set[int] = set()             # tenders we hold: our own unwind moves their price

    @property
    def book_tickers(self) -> list[str]:
        return self.tickers

    def _update_drift(self, snap: Snapshot) -> None:
        if snap.tick == self.last_tick:          # one time-series observation per tick
            return
        self.last_tick = snap.tick
        if self.crowd_learn:
            self._learn_crowd(snap)
        for t in self.tickers:
            m = snap.mid(t)
            if m is None:
                continue
            self.garch[t].add_price(m)
            if t in self.last_mid:
                self.drift[t].update(m - self.last_mid[t])
            self.last_mid[t] = m

    def _learn_crowd(self, snap: Snapshot) -> None:
        lag, keep = int(self.p.get("crowd_ticks", 10)), []
        for tick, t, sign, x, m0, tid in self.crowd_obs:
            m = snap.mid(t)
            if snap.abs_tick - tick < lag or m is None:
                keep.append((tick, t, sign, x, m0, tid))
                continue
            if tid in self.taken and self.p.get("crowd_untaken_only", False):
                continue          # our own unwind's impact is already priced by walking the book
            self.crowd.update(x, sign * (m - m0))       # + = the price ran against the unwind
            log.info("CROWD %s moved %+.3f against a %.0fk-share unwind -> %.4f $/10k (sd %.4f, n=%d)",
                     t, sign * (m - m0), 10 * x, self.crowd.mean, self.crowd.sd, self.crowd.n)
        self.crowd_obs = keep

    def crowd_cost(self, qty: int) -> float:
        """Expected $/share the crowd's unwind costs us on a tender of `qty` shares."""
        if not self.crowd_learn or (self.p.get("crowd_gate", False) and not self.crowded()):
            return 0.0
        return self.p.get("crowd_weight", 1.0) * max(0.0, self.crowd.mean) * qty / 10_000

    def crowded(self) -> bool:
        """The learned crowd impact is significantly positive (posterior mean > 2 sd)."""
        return self.crowd_learn and self.crowd.n > 0 and self.crowd.mean > 2 * self.crowd.sd

    def unwind_horizon(self) -> int:
        """Race the crowd: once it is known to unwind into our book, finish before it does."""
        fast = int(self.cfg.get("execution", {}).get("crowd_horizon_ticks", 0))
        return min(self.horizon, fast) if fast and self.crowded() else self.horizon

    def step(self, snap: Snapshot) -> None:
        self._update_drift(snap)
        self.handle_tenders(snap)
        self.unwind(snap)

    def price_vol(self, ticker: str, snap: Snapshot) -> float:
        g, mid = self.garch.get(ticker), snap.mid(ticker)
        return g.vol() * mid if g is not None and g.ready and mid else 0.0

    def handle_tenders(self, snap: Snapshot) -> None:
        positions = snap.positions
        for t in self.current_tenders():
            tid = int(t.get("tender_id", -1))
            ticker = t.get("ticker")
            if tid in self.seen or ticker is None:
                continue
            book = snap.book(ticker)
            if self.crowd_learn and snap.mid(ticker) is not None:
                # The price runs AGAINST our unwind: down after a BUY tender (we must sell).
                sign = -1 if str(t.get("action", "BUY")).upper() == "BUY" else 1
                self.crowd_obs.append((snap.abs_tick, ticker, sign, int(t.get("quantity", 0)) / 10_000,
                                       snap.mid(ticker), tid))
            d = evaluate_tender(
                t, book,
                fee=float(self.fees.get(ticker, 0.0)),
                position=positions.get(ticker, 0),
                room=self.risk.room(ticker, t.get("action", "BUY"), positions),
                ticks_left=snap.ticks_left,
                refill_factor=self.p.get("refill_factor", 2.0),
                # In a drawdown, only the best tenders: required margin scales with 1/throttle.
                min_profit=self.p.get("min_profit_per_share", 0.02) * self.edge_mult(),
                competitive_margin=self.p.get("competitive_margin", 0.05),
                drift_per_tick=(self.drift[ticker].mean or 0.0) if ticker in self.drift else 0.0,
                unwind_ticks_per_lot=self.p.get("unwind_ticks_per_share", 0.0),
                min_ticks_to_unwind=self.p.get("min_ticks_to_unwind", 10),
                price_vol_per_tick=self.price_vol(ticker, snap),
                risk_aversion=self.p.get("risk_aversion", 0.0),
                crowd_cost=self.crowd_cost(int(t.get("quantity", 0))),
            )
            self.seen.add(tid)
            log.info("TENDER %s %s %s x%s @ %s -> %s (%s)", tid, t.get("action"), ticker,
                     t.get("quantity"), t.get("price"), "ACCEPT" if d.accept else "decline", d.reason)
            if self.ex.dry_run:
                continue
            try:
                if d.accept:
                    resp = self.client.accept_tender(tid, None if t.get("is_fixed_bid", True) else d.price)
                    if isinstance(resp, dict) and resp.get("success") is False:
                        log.info("TENDER %s not filled (competitive bid %s rejected)", tid, d.price)
                        continue          # we hold nothing: don't spend risk room on it
                    self.taken.add(tid)
                    sign = 1 if str(t.get("action")).upper() == "BUY" else -1
                    positions[ticker] = positions.get(ticker, 0) + sign * int(t["quantity"])
                elif self.p.get("decline_explicitly", True):
                    self.client.decline_tender(tid)
            except Exception as exc:       # tender may have expired between read and accept
                log.warning("tender %s action failed: %s", tid, exc)

    def unwind(self, snap: Snapshot) -> None:
        if self.mode == "block":
            self.unwind_block(snap)
        else:
            self.unwind_slices(snap)

    def unwind_block(self, snap: Snapshot) -> None:
        now = snap.abs_tick
        period_end = snap.period * snap.ticks_per_period - int(self.cfg.get("run", {}).get("wind_down_ticks", 5)) - 1
        for t in self.tickers:
            pos = snap.positions.get(t, 0)
            b = self.algo.blocks.get(t)
            if pos == 0:
                if b:
                    self.algo.cancel(t)
                continue
            # New / grown / flipped exposure (e.g. another tender accepted): restart the schedule.
            if b is None or (pos > 0) != (b.start_pos > 0) or abs(pos) > abs(b.start_pos):
                kappa = 0.0
                if self.schedule == "almgren_chriss":
                    # sigma: GARCH $/share/tick; eta: from the side we unwind INTO.
                    kappa = ac_kappa(self.price_vol(t, snap), book_eta(snap.book(t), "bid" if pos > 0 else "ask"),
                                     self.ac_lambda)
                self.algo.work(t, pos, 0, now, min(now + self.unwind_horizon(), period_end), kappa=kappa)
        if self.algo.active:
            open_ids = None if self.ex.dry_run else {int(o["order_id"]) for o in self.client.orders("OPEN")}
            self.algo.step({t: snap.book(t) for t in self.algo.blocks}, snap.positions, now, open_ids)

    def unwind_slices(self, snap: Snapshot) -> None:
        urgency = self.p.get("urgent_ticks", 30)
        for ticker in self.tickers:
            pos = snap.positions.get(ticker, 0)
            if pos == 0:
                continue
            slip = self.p.get("max_slippage", 0.05)
            part = self.p.get("participation", 0.3)
            if snap.ticks_left < urgency:          # get flat before the bell
                scale = 1 + 3 * (1 - snap.ticks_left / max(urgency, 1))
                slip, part = slip * scale, min(1.0, part * scale)
            o = unwind_slice(snap.book(ticker), pos, slip, part, self.ex.max_size(ticker))
            if o:
                self.ex.limit(ticker, o[0], o[1], o[2])

    def wind_down(self, snap: Snapshot) -> None:
        # Last ticks: dump what is left - an unclosed position is pure risk.
        for t in list(self.algo.blocks):
            self.algo.cancel(t)
        self.ex.cancel_all()
        for ticker in self.tickers:
            pos = snap.positions.get(ticker, 0)
            if pos:
                book = snap.book(ticker)
                touch = book.best_bid if pos > 0 else book.best_ask
                if touch:
                    px = touch - 0.25 if pos > 0 else touch + 0.25
                    self.ex.limit(ticker, "SELL" if pos > 0 else "BUY", abs(pos), px)
