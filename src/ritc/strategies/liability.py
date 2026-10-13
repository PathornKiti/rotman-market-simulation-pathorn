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

Hold to the bell (2027 brief)
-----------------------------
"All open positions are closed at the end ... based on the last traded price" with
no fine, and prices are a random walk. So holding inventory costs NOTHING on
average, while every crossed share pays fee + spread (+ impact). Crossing only
buys risk reduction. So:

    hold_risk_budget   cross only the part of a position whose 1-sd risk to the bell,
                       |pos| x GARCH sigma x sqrt(ticks left), is over the budget ($);
                       the rest only RESTS at the touch (earns the spread, no impact)
    close_hold_ticks   in the final seconds never cross at all
    hold_valuation     inside that window a tender's exit is the close-out at ~mid
                       (no fee, no book to walk) less the risk premium of holding

A volatile market (GARCH sigma up) shrinks the held part automatically.

Tenders arrive through the real-time feed (polled every ~100 ms). By default a
block is evaluated and answered as soon as it appears.

Deciding late (`decide_late_ticks`)
-----------------------------------
A tender's price is fixed when it arrives, but the market keeps moving for the
whole 15-30 s decision window. Answering at once throws that away: wait until
`decide_late_ticks` before expiry, re-price against the book THEN, and accept
only if it still pays. Under a random walk that is an option on the tender
(accept the ones that drifted our way, drop the ones that drifted away), and a
crowd unwinding the same block shows up in the price before we commit. The cost:
the stock is frozen while we wait (`respect_windows`: trading it during an open
window is front-running).

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
(docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import logging
import math
import time
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
    close_hold_ticks: int = 0,
    flow_rate: float = 0.0,
    drift_symmetric: bool = False,
) -> TenderDecision:
    """
    Pure decision function. `tender['action']` is OUR side of the trade:
    BUY means we buy the block from the client (and must later SELL it).
    `drift_per_tick` is the recent mid drift (+ = rising); it is a cost when it
    runs against our unwind direction over the expected unwind time.
    `flow_rate` > 0 (FORWARD LIQUIDITY, shares/tick the market trades on our unwind side): we unwind by resting
    at the touch, so the exit is the touch we rest at, and the time it takes is to_unwind / flow_rate (the risk
    premium and drift grow with it). 0 = the old static model: walk the visible book x `refill_factor`.
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

    if to_unwind > 0 and flow_rate > 0:
        vwap_unwind = (book.best_ask if action == "BUY" else book.best_bid) or 0.0   # where our resting unwind sits
    elif to_unwind > 0:
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
    unwind_ticks = to_unwind / flow_rate if flow_rate > 0 else to_unwind * unwind_ticks_per_lot
    # TREND (legal use): `drift_symmetric` also credits a trend that runs our way while we unwind.
    drift_cost = (-sign * drift_per_tick if drift_symmetric else max(0.0, -sign * drift_per_tick)) * unwind_ticks
    drift_cost += risk_aversion * price_vol_per_tick * unwind_ticks ** 0.5
    drift_cost += crowd_cost             # learned: how far the price runs against tenders' unwinds
    fees = fee * (to_unwind / qty)       # we pay the taker fee only on what hits the book

    # Close to the bell the exit is the case's own close-out at the last traded price (~mid): no fee,
    # no book to walk, only the price risk of holding to the end. Value it that way when it is better.
    if close_hold_ticks and ticks_left <= close_hold_ticks and book.mid:
        hold_cost = risk_aversion * price_vol_per_tick * ticks_left ** 0.5 + crowd_cost
        if sign * book.mid - hold_cost > sign * vwap_unwind - fees - drift_cost:
            vwap_unwind, fees, drift_cost = book.mid, 0.0, hold_cost

    if fixed:
        if tender_px is None:
            return TenderDecision(False, None, 0.0, vwap_unwind, "fixed tender without price")
        pps = sign * (vwap_unwind - float(tender_px)) - fees - drift_cost
        ok = pps >= min_profit
        why = f"profit/share {pps:+.4f} {'>=' if ok else '<'} {min_profit:.4f}"
        return TenderDecision(ok, float(tender_px), pps, vwap_unwind, why)

    # Competitive: bid the price that still leaves our margin.
    bid = vwap_unwind - sign * (fees + drift_cost + competitive_margin)
    ref = float(tender_px) if tender_px is not None else 0.0
    if ref > 0 and book.mid and abs(ref / book.mid - 1) < 0.2:
        # Some cases show a reference price; never bid worse for ourselves than it. A zero or absurd one
        # (the API's placeholder on an auction?) is ignored: min(bid, 0) would bid $0 and never win.
        bid = min(bid, ref) if action == "BUY" else max(bid, ref)
    pps = sign * (vwap_unwind - bid) - fees - drift_cost
    ok = pps >= min_profit
    return TenderDecision(ok, round(bid, 2), pps, vwap_unwind, f"competitive bid {bid:.2f}, profit/share {pps:+.4f}")


def hold_within_budget(qty: int, price_vol_per_tick: float, ticks_left: int, budget: float) -> bool:
    """
    Holding `qty` to the bell risks |qty| x sigma x sqrt(ticks left) (1 sd, $). Under a random walk, with
    the case closing what is left at the last price for free, holding costs nothing on average and
    crossing always costs fee + spread (+ impact). So only cross while that risk is over `budget`.
    No vol estimate yet -> not within budget (unwind as usual).
    """
    if price_vol_per_tick <= 0:
        return False
    return abs(qty) * price_vol_per_tick * max(ticks_left, 0) ** 0.5 <= budget


def unwind_stuck(hist: list[tuple[int, int]], now: int, window: int, horizon: float) -> bool:
    """
    Absorption stop for the passive hold. `hist` = (abs tick, shares still to unwind) of the current block.
    The market absorbed (remaining `window` ticks ago - remaining now) / window shares per tick into our
    resting orders; if that rate needs more than `horizon` ticks to finish, the block is stuck. In the
    practice heats the only two slow unwinds (701 / 893 shares/s) were the two big adverse moves: when
    nobody trades on our side the price is running away, so holding is no longer free (docs/RESEARCH.md).
    """
    if window <= 0 or not hist or now - hist[0][0] < window:
        return False
    rem = hist[-1][1]
    past = next((r for k, r in reversed(hist) if now - k >= window), hist[0][1])
    rate = max(past - rem, 0) / window
    return rem > 0 and (rate <= 0 or rem / rate > horizon)


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
        self.seen: set[int] = set()              # tenders we have ANSWERED (accepted / declined / bid)
        self.observed: set[int] = set()          # tenders whose crowd reaction we are already scoring
        self.first_seen: dict[int, int] = {}     # tender id -> abs tick we first saw it (decide_late safety)
        ex_cfg = self.cfg.get("execution", {})
        self.mode = ex_cfg.get("unwind_mode", "block")
        self.horizon = int(ex_cfg.get("unwind_horizon_ticks", 30))
        self.schedule = ex_cfg.get("schedule", "almgren_chriss")          # or "almgren_chriss"
        self.ac_lambda = float(ex_cfg.get("ac_risk_aversion", 3e-7))
        self.close_hold = int(ex_cfg.get("close_hold_ticks", 0))   # passive only this close to the bell
        self.hold_budget = float(ex_cfg.get("hold_risk_budget", 0.0))   # $ 1-sd risk to the bell held passively
        self.fresh_unwind = bool(ex_cfg.get("fresh_unwind", False))   # re-read positions before sizing orders
        self.hold_until = int(ex_cfg.get("hold_budget_min_ticks", 0))   # flat by the bell: no hold this close
        self.stuck_window = int(ex_cfg.get("stuck_window_ticks", 0))   # absorption stop (0 = off)
        self.stuck_horizon = float(ex_cfg.get("stuck_horizon_ticks", 30))
        self.unwind_hist: dict[str, list[tuple[int, int]]] = {}   # ticker -> (tick, shares left) of its block
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
        self._tas_t = 0.0                        # last time & sales prefetch (monotonic s)
        self.ledger: dict[int, dict] = {}        # tender id -> what we took, expected $, realized $, auction won
        self.block_tids: dict[str, list[int]] = {}   # ticker -> tenders in its current block
        self.auction_watch: dict[int, list] = {}  # auction id -> [ticker, sign, qty, expiry tick, position before]
        self.pnl_base: dict[str, tuple[float, int]] = {}   # ticker -> (stock P&L when its tender was taken, tender id)
        self.crowd_sigma: dict[int, float] = {}  # tender id -> GARCH $ vol/tick when it appeared (crowd_robust)
        self.taken: set[int] = set()             # tenders we hold: our own unwind moves their price
        # Accepted tenders the server may not have booked yet: id -> (ticker, signed qty, abs tick by which
        # it is surely booked). RIT books ~1 s after the accept (winner-take-all only at the window's end).
        self.pending: dict[int, tuple[str, int, int]] = {}
        self.bidding: dict[int, tuple[str, int]] = {}    # our open auction bids: id -> (ticker, abs expiry tick)
        self.frozen: set[str] = set()                   # tickers with an undecided tender (respect_windows)
        self.journaled: set[int] = set()                # tenders already written to the run journal
        # FORWARD LIQUIDITY (docs/PRACTICE_LOGS.md): shares/tick the market trades on each side of each stock, from
        # time & sales, as an EWMA started at the practice-measured absorption of our resting unwinds. "SELL" =
        # buyer-initiated volume (it fills a resting sell). Used to price tenders (`liquidity_model = "flow"`).
        self.liq_model = s.get("liquidity_model", "book")
        self.liq_prior: dict[str, float] = dict(s.get("liq_prior", {}))
        self.liq: dict[tuple[str, str], float] = {}
        self.tas_after: dict[str, int] = {}
        self.liq_tick: dict[str, int] = {}

    @property
    def book_tickers(self) -> list[str]:
        return self.tickers

    def sync_tickers(self, snap: Snapshot) -> None:
        """
        RITC 2026: each sub-heat trades 2-4 DIFFERENT stocks, so trade what the server lists, not
        the config. A tender on a ticker we don't track would be accepted and never unwound
        ($10/share uncovered fine). Fees and order sizes come from the server row unless the
        config sets them; every stock counts 1 share against the limit groups.
        """
        live = [t for t, s in snap.securities.items()
                if s.get("type", "STOCK") == "STOCK" and s.get("is_tradeable", True)]
        held = [t for t, q in snap.positions.items() if q and t not in live]
        tickers = live + held
        if tickers == self.tickers:
            return
        for t in tickers:
            if t in self.drift:
                continue
            row = snap.securities.get(t, {})
            if t not in self.fees and row.get("trading_fee") is not None:
                self.fees[t] = float(row["trading_fee"])
            if t not in self.ex.max_order_size and row.get("max_trade_size"):
                self.ex.max_order_size[t] = int(row["max_trade_size"])
            self.risk.add_ticker(t)
            self.drift[t] = EWMA(self.p.get("drift_halflife", 15))
            self.garch[t] = OnlineGarch()
        log.info("tickers: %s", ", ".join(tickers))
        self.tickers = tickers

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
        if self.liq_model in ("flow", "auctions", "adaptive"):
            self._update_liquidity(snap)
        if snap.tick % 60 == 0:      # practice check: compare with `calibrate`'s measured sigma/tick per stock
            log.info("VOL " + "  ".join(f"{t} ${self.price_vol(t, snap):.4f}/tick" for t in self.tickers))
            if self.liq_model in ("flow", "auctions", "adaptive"):
                log.info("LIQ " + "  ".join(f"{t} sell {self.flow(t, 'SELL'):,.0f} buy {self.flow(t, 'BUY'):,.0f}/tick"
                                            for t in self.tickers)
                         + (f"  adaptive -> {'flow' if self.no_crowd_seen() else 'book'}"
                            if self.liq_model == "adaptive" else ""))

    def flow(self, ticker: str, side: str) -> float:
        """Forecast shares/tick that will trade against a resting `side` order in `ticker` (side = our unwind)."""
        prior = float(self.liq_prior.get(ticker, self.p.get("liq_default", 2000)))
        return min(self.liq.get((ticker, side), prior), float(self.p.get("liq_cap", 6000)))

    def prefetch(self) -> dict:
        """Time & sales for the forward-liquidity models, in the snapshot's parallel batch (no extra round trip)."""
        if self.liq_model not in ("flow", "auctions", "adaptive"):
            return {}
        if time.monotonic() - self._tas_t < 0.9:          # trade flow is read once per tick: skip in between
            return {}
        self._tas_t = time.monotonic()
        return {f"tas:{t}": (lambda t=t: self.client.tas(t, after=self.tas_after.get(t))) for t in self.tickers}

    def _update_liquidity(self, snap: Snapshot) -> None:
        """One time & sales read per stock per tick: buyer- vs seller-initiated volume (price vs the mid)."""
        if all(f"tas:{t}" in snap.extra for t in self.tickers):
            res = [snap.extra[f"tas:{t}"] for t in self.tickers]
        else:
            res = self.client.parallel([lambda t=t: self.client.tas(t, after=self.tas_after.get(t))
                                        for t in self.tickers])
        alpha = 1 - 0.5 ** (1 / max(1.0, float(self.p.get("liq_halflife", 20))))
        for t, rows in zip(self.tickers, res):
            mid = snap.mid(t)
            if not isinstance(rows, list) or mid is None:
                continue
            new = [r for r in rows if int(r.get("id", 0)) > self.tas_after.get(t, -1)]
            first = t not in self.tas_after
            if rows:
                self.tas_after[t] = max([self.tas_after.get(t, -1)] + [int(r.get("id", 0)) for r in rows])
            last, self.liq_tick[t] = self.liq_tick.get(t), snap.abs_tick
            if first or last is None:
                continue                                    # history before we started: not a rate
            dt = max(1, snap.abs_tick - last)
            buys = sum(float(r.get("quantity", 0)) for r in new if float(r.get("price", 0)) >= mid)
            sells = sum(float(r.get("quantity", 0)) for r in new if float(r.get("price", 0)) < mid)
            for side, vol in (("SELL", buys), ("BUY", sells)):
                cur = self.flow(t, side)
                self.liq[(t, side)] = cur + (1 - (1 - alpha) ** dt) * (vol / dt - cur)

    def _learn_crowd(self, snap: Snapshot) -> None:
        lag, keep = int(self.p.get("crowd_ticks", 10)), []
        for tick, t, sign, x, m0, tid in self.crowd_obs:
            m = snap.mid(t)
            if snap.abs_tick - tick < lag or m is None:
                keep.append((tick, t, sign, x, m0, tid))
                continue
            if tid in self.taken and self.p.get("crowd_untaken_only", False):
                continue          # our own unwind's impact is already priced by walking the book
            move = sign * (m - m0)                      # + = the price ran against the unwind
            if self.p.get("crowd_robust", False):
                # ROBUST CROWD: a move is evidence only relative to the stock's own volatility over the window.
                # Practice 2026-10-12: one CROC move of +0.64 (vol spiking to $0.07/tick) read as a 6-sd crowd
                # under the fixed 0.10 noise and charged ~$0.6/share on every later tender of the heat.
                s_move = self.crowd_sigma.pop(tid, 0.0) * lag ** 0.5
                clip = float(self.p.get("crowd_clip", 2.5)) * s_move
                if clip > 0:
                    move = max(-clip, min(clip, move))
                if self.p.get("crowd_robust_mode", "full") == "full":
                    self.crowd.noise_sd = max(float(self.p.get("crowd_noise_sd", 0.10)), s_move)
                # "clip": only cap outliers at crowd_clip sd; keep the noise level, so a real crowd in a volatile
                # market is not explained away (2026-10-13 stress: "full" lost -$30k in "everything at once")
            self.crowd.update(x, move)
            log.info("CROWD %s moved %+.3f against a %.0fk-share unwind -> %.4f $/10k (sd %.4f, n=%d)%s",
                     t, sign * (m - m0), 10 * x, self.crowd.mean, self.crowd.sd, self.crowd.n,
                     " CROWDED" if self.crowded() else "")
        self.crowd_obs = keep

    def crowd_cost(self, qty: int) -> float:
        """Expected $/share the crowd's unwind costs us on a tender of `qty` shares."""
        if not self.crowd_learn or (self.p.get("crowd_gate", False) and not self.crowded()):
            return 0.0
        return self.p.get("crowd_weight", 1.0) * max(0.0, self.crowd.mean) * qty / 10_000

    def crowded(self) -> bool:
        """The learned crowd impact is significantly positive (posterior mean > 2 sd)."""
        return (self.crowd_learn and self.crowd.n >= max(1, int(self.p.get("crowd_min_obs", 1)))
                and self.crowd.mean > 2 * self.crowd.sd)

    def trend_against(self, ticker: str, position: int, snap: Snapshot) -> float:
        """
        TREND (legal use, professor 2026-10-12): z-score of the recent drift running AGAINST `position` (falling
        while we are long). The drift is an EWMA of 1-tick mid changes with half-life `drift_halflife`, which
        averages ~2.9 x half-life ticks, so its standard error is sigma / sqrt(2.9 h). Used only to unwind
        faster (cross the schedule's shortfall instead of resting); never to open a position.
        """
        d, sig = self.drift.get(ticker), self.price_vol(ticker, snap)
        if d is None or d.mean is None or sig <= 0 or not position:
            return 0.0
        h = float(self.p.get("drift_halflife", 15))
        return (-1 if position > 0 else 1) * d.mean / (sig / math.sqrt(2.9 * h))

    def no_crowd_seen(self) -> bool:
        """
        `liquidity_model = "adaptive"`: price tenders off forward liquidity ("flow", the higher-mean model) only
        once `adaptive_min_obs` untaken tenders have been scored and the learned crowd impact is under one sd
        (evidence leaning to no crowd); until then, and whenever a crowd shows, the pessimistic book walk.
        """
        n = int(self.p.get("adaptive_min_obs", 3))
        return self.crowd_learn and self.crowd.n >= n and self.crowd.mean < self.crowd.sd

    def unwind_horizon(self) -> int:
        """Race the crowd: once it is known to unwind into our book, finish before it does."""
        fast = int(self.cfg.get("execution", {}).get("crowd_horizon_ticks", 0))
        return min(self.horizon, fast) if fast and self.crowded() else self.horizon

    def step(self, snap: Snapshot) -> None:
        self.sync_tickers(snap)
        self._update_drift(snap)
        self._watch_auctions(snap)
        self.handle_tenders(snap)
        self.frozen = self.undecided(snap) if self.p.get("respect_windows", False) else set()
        for t in self.frozen:
            if t in self.algo.blocks:
                self.algo.cancel(t)                  # pull resting orders: a fill now is front-running
        self.unwind(snap)

    def undecided(self, snap: Snapshot) -> set[str]:
        """
        Tickers with a tender still in its decision window for us: offered and not yet answered, or an
        auction / winner-take-all we bid on that is not resolved until it expires. The brief: "Any trades
        executed during this window, before the tender decision is finalized, are flagged as front-running".
        """
        now = snap.abs_tick
        self.bidding = {k: v for k, v in self.bidding.items() if v[1] >= now}
        out = {t for t, _ in self.bidding.values()}
        out |= {t.get("ticker") for t in self.current_tenders()
                if int(t.get("tender_id", -1)) not in self.seen and t.get("ticker")}
        return out

    def price_vol(self, ticker: str, snap: Snapshot) -> float:
        g, mid = self.garch.get(ticker), snap.mid(ticker)
        return g.vol() * mid if g is not None and g.ready and mid else 0.0

    def pending_delta(self, ticker: str, now: int, room: bool = False) -> int:
        """
        Signed shares of accepted tenders on `ticker` that may still be unbooked. `room`: for the risk-room
        check, where `reserve_auction_room = false` leaves out auction bids still in their window (if the
        server voids an award that no longer fits, an open bid uses no room until it actually fills).
        """
        self.pending = {k: v for k, v in self.pending.items() if v[2] > now}
        skip = room and not self.p.get("reserve_auction_room", True)
        return sum(q for k, (t, q, _) in self.pending.items()
                   if t == ticker and not (skip and k in self.bidding and self.bidding[k][1] > now))

    def unwind_target(self, ticker: str, pos: int, now: int) -> int:
        """
        Where to unwind `pos` to. Normally flat; but an accepted, not-yet-booked tender in the other
        direction will offset part of it when it lands, so keep that part. Trading it away first would
        take the position through zero once the tender books: fined as speculation (2027 brief), and a
        round trip of spread and fees. Never past zero of the current position, whatever is pending.
        """
        d = self.pending_delta(ticker, now)
        if not d or not pos or (d > 0) == (pos > 0):
            return 0
        return (1 if pos > 0 else -1) * min(abs(pos), abs(d))

    def wait_to_decide(self, t: dict, tid: int, snap: Snapshot) -> bool:
        """
        decide_late_ticks: hold the answer until that many ticks before `expires`. Safety for the real
        server, whose `expires` we have not seen yet: answer AT ONCE if the window looks wrong (expires
        missing, already inside `late`, or more than `decide_late_max_window` ticks away), and never wait
        longer than that since we first saw the tender. A missed tender is a declined one: never risk it.
        """
        late = int(self.p.get("decide_late_ticks", 0))
        if not late:
            return False
        if tid not in self.first_seen:
            log.info("TENDER %s %s %s x%s @ %s seen at tick %s, expires %s: answering %s ticks before it", tid,
                     t.get("action"), t.get("ticker"), t.get("quantity"), t.get("price"), snap.tick,
                     t.get("expires"), late)
        first = self.first_seen.setdefault(tid, snap.tick)
        cap = int(self.p.get("decide_late_max_window", 30))
        try:
            left = int(t["expires"]) - snap.tick
        except (KeyError, TypeError, ValueError):
            return False
        if left <= late or left > cap or snap.tick - first >= cap - late:
            return False
        if self.p.get("decide_late_end_guard", True):
            # Don't wait past the last tick a tender can still be ACCEPTED: evaluate_tender refuses with fewer
            # than `min_ticks_to_unwind` left, and the wind-down stops answering. A window running past the
            # bell would otherwise be answered too late (real-time test, 2026-10-11: 3 of 32 missed).
            last_ok = max(int(self.p.get("min_ticks_to_unwind", 10)),
                          int(self.cfg.get("run", {}).get("wind_down_ticks", 5))) + 1
            if snap.ticks_left <= last_ok + late:
                return False
        return True

    def wait_for_room(self, t: dict, d: TenderDecision, snap: Snapshot) -> bool:
        """
        `room_wait`: a fixed tender declined ONLY for risk room is re-checked every tick until `room_wait_margin`
        ticks before it expires, instead of declined at once. Practice heats (log2 #103, last heat #403): the room
        was held by an auction bid we then lost, or by a block that unwinds in ~20 s. Only when we hold none of the
        tender's stock: waiting freezes that stock (respect_windows), which would stall its own unwind.
        """
        if not self.p.get("room_wait", False) or not t.get("is_fixed_bid", True):
            return False
        if not d.reason.startswith("exceeds risk room") or snap.positions.get(t.get("ticker"), 0):
            return False
        try:
            left = int(t["expires"]) - snap.tick
        except (KeyError, TypeError, ValueError):
            return False
        last_ok = max(int(self.p.get("min_ticks_to_unwind", 10)),
                      int(self.cfg.get("run", {}).get("wind_down_ticks", 5))) + 1
        return left > int(self.p.get("room_wait_margin", 2)) and snap.ticks_left > last_ok + 1

    def decide(self, t: dict, snap: Snapshot, positions: dict[str, int]) -> TenderDecision:
        """The bot's answer to tender `t` against the book of now (also used by `ritc suggest`)."""
        skip = float(self.p.get("auction_skip_adding", 0))
        if skip and not t.get("is_fixed_bid", True):
            # AUCTION FREEZE (practice 2026-10-13, -$15k): an open auction bid freezes its stock for the whole
            # window (front-running rule), so bidding one that would ADD to a block we are still unwinding stops
            # that unwind for ~30 s while the price can run, and a win only makes the block bigger. Skip it.
            sign = 1 if str(t.get("action", "BUY")).upper() == "BUY" else -1
            pos = positions.get(t.get("ticker"), 0)
            if sign * pos >= skip:
                return TenderDecision(False, None, 0.0, 0.0, f"auction would add to our {pos:+d} block")
        if self.liq_model == "ensemble":
            return self.decide_ensemble(t, snap, positions)
        return self._evaluate(t, snap, positions)

    def _evaluate(self, t: dict, snap: Snapshot, positions: dict[str, int], flow: bool | None = None,
                  crowd: bool = True, room: int | None = None) -> TenderDecision:
        """evaluate_tender with the bot's state. `flow`: force the forward-liquidity exit on / off (None = the
        liquidity_model's choice); `crowd`: include the learned crowd charge; `room`: override the risk room."""
        ticker = t.get("ticker")
        use_flow = ((self.liq_model == "flow" or (self.liq_model == "auctions" and self.bid_up(t, positions))
                     or (self.liq_model == "adaptive" and self.no_crowd_seen())) if flow is None else flow)
        return evaluate_tender(
            t, snap.book(ticker),
            fee=float(self.fees.get(ticker, 0.0)),
            position=positions.get(ticker, 0),
            room=self.risk.room(ticker, t.get("action", "BUY"), positions) if room is None else room,
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
            crowd_cost=self.crowd_cost(int(t.get("quantity", 0))) if crowd else 0.0,
            close_hold_ticks=self.close_hold if self.p.get("hold_valuation", False) else 0,
            flow_rate=(self.flow(ticker, "SELL" if str(t.get("action", "BUY")).upper() == "BUY" else "BUY")
                       if use_flow else 0.0),
            drift_symmetric=bool(self.p.get("drift_symmetric", False)),
        )

    def scenario_weights(self) -> dict[str, float]:
        """
        ENSEMBLE weights (professor's multi-pricing): how likely each world is right now.
        crowd: the learned crowd posterior's probability that its impact is above `ensemble_crowd_min`
        ($/10k), once `crowd_min_obs` tenders are scored (`ensemble_crowd_prior` before that); the rest is
        split between the cautious book walk and forward liquidity by `ensemble_flow_weight`.
        """
        p = float(self.p.get("ensemble_crowd_prior", 0.15))
        if self.crowd_learn and self.crowd.n >= max(1, int(self.p.get("crowd_min_obs", 1))):
            z = (self.crowd.mean - float(self.p.get("ensemble_crowd_min", 0.02))) / max(self.crowd.sd, 1e-9)
            p = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        p = min(max(p, 0.02), 0.98)
        wf = float(self.p.get("ensemble_flow_weight", 0.5))
        return {"book": (1 - p) * (1 - wf), "flow": (1 - p) * wf, "crowd": p}

    def decide_ensemble(self, t: dict, snap: Snapshot, positions: dict[str, int]) -> TenderDecision:
        """
        `liquidity_model = "ensemble"`: value the tender in three worlds and decide on the weighted value with
        a worst-case floor (the professor's multi-pricing, practice 2026-10-12):
          book   walk the visible book x refill (cautious)
          flow   rest at the touch, unwind at the forecast trade flow (liquid market)
          crowd  other desks took the same block: the book must absorb (1 + `ensemble_crowd_extra`) x our size
                 ("our 50k becomes 70k")
        Fixed tender: accept iff sum(w x profit) >= min_profit and every world loses at most `ensemble_floor`/share.
        Auction: bid the weighted break-even less the margin, but never past the price at which the worst world
        loses more than `ensemble_floor` (profit is linear in the bid, so both bounds are exact).
        """
        room = self.risk.room(t.get("ticker"), t.get("action", "BUY"), positions)
        qty = int(t.get("quantity", 0))
        if qty > room:
            return TenderDecision(False, None, 0.0, 0.0, f"exceeds risk room ({qty} > {room})")
        big = dict(t, quantity=int(qty * (1 + float(self.p.get("ensemble_crowd_extra", 0.4)))))
        sc = {"book": self._evaluate(t, snap, positions, flow=False, crowd=False, room=10**9),
              "flow": self._evaluate(t, snap, positions, flow=True, crowd=False, room=10**9),
              "crowd": self._evaluate(big, snap, positions, flow=False, crowd=False, room=10**9)}
        if any(d.unwind_vwap == 0.0 for d in sc.values()):        # no market / too late: the cautious answer
            return sc["book"]
        w = self.scenario_weights()
        floor = float(self.p.get("ensemble_floor", 0.15))
        min_profit = self.p.get("min_profit_per_share", 0.02) * self.edge_mult()
        sign = 1 if str(t.get("action", "BUY")).upper() == "BUY" else -1
        fixed = bool(t.get("is_fixed_bid", True))
        if fixed:
            price = sc["book"].price
        else:
            margin = self.p.get("competitive_margin", 0.05)
            # each world's profit at bid b: pps_i + sign x (bid_i - b); EV = margin at b_ev, worst = -floor at b_wc
            b_ev = sum(w[k] * (sc[k].price + sign * (sc[k].profit_per_share - margin)) for k in sc)
            b_wc = min(sign * (sc[k].price + sign * (sc[k].profit_per_share + floor)) for k in sc) * sign
            price = round(min(b_ev, b_wc) if sign > 0 else max(b_ev, b_wc), 2)
        pps = {k: (d.profit_per_share if fixed else d.profit_per_share + sign * (d.price - price))
               for k, d in sc.items()}
        ev = sum(w[k] * pps[k] for k in sc)
        worst = min(pps.values())
        ok = ev >= min_profit and worst >= -floor
        log.info("SCEN %s %s %s %dk: book %+.3f flow %+.3f crowd %+.3f | w %.2f/%.2f/%.2f -> EV %+.3f worst %+.3f %s",
                 t.get("tender_id"), t.get("action"), t.get("ticker"), qty // 1000, pps["book"], pps["flow"],
                 pps["crowd"], w["book"], w["flow"], w["crowd"], ev, worst, "ACCEPT" if ok else "decline")
        vwap = sum(w[k] * sc[k].unwind_vwap for k in sc)
        why = (f"ensemble EV {ev:+.4f} (worst {worst:+.4f})" if fixed
               else f"competitive bid {price:.2f}, ensemble EV {ev:+.4f} (worst {worst:+.4f})")
        return TenderDecision(ok, price, ev, vwap, why)

    def bid_up(self, t: dict, positions: dict[str, int]) -> bool:
        """
        `liquidity_model = "auctions"`: bid an auction at its forward-liquidity value (wins far more often) only when
        winning leaves the net limit free for the next fixed tender. Practice-calibrated simulator: an auction won
        near the reserve earns ~$6k, but its 55k shares fill half the 100k net limit while held, and the fixed
        tenders it crowds out are worth ~$27k each (PRACTICE_LOGS.md section 3). So only when |net after the win|
        <= `auction_max_net`: typically an auction that offsets what we already hold.
        """
        if t.get("is_fixed_bid", True):
            return False
        sign = 1 if str(t.get("action", "BUY")).upper() == "BUY" else -1
        net_after = sum(positions.values()) + sign * int(t.get("quantity", 0))
        return abs(net_after) <= int(self.p.get("auction_max_net", 10**9))

    def _watch_auctions(self, snap: Snapshot) -> None:
        """
        AUCTION RESULT in the log: RIT books an award at the window's close and says nothing else, so compare the
        stock's position just before expiry with a few ticks after (the stock is frozen meanwhile, so nothing else
        moves it). Won = it moved by >= 80% of the auction's size in the bid's direction.
        """
        now = snap.abs_tick
        for tid, w in list(self.auction_watch.items()):
            ticker, sign, qty, expiry, before = w
            if before is None and now >= expiry - 1:
                w[4] = before = snap.positions.get(ticker, 0)
            if before is not None and now >= expiry + 3:
                after = snap.positions.get(ticker, 0)
                won = sign * (after - before) >= 0.8 * qty
                e = self.ledger.get(tid)
                if e is not None:
                    e["won"] = won
                log.info("AUCTION #%s %s %s %dk at %s: %s", tid, "BUY" if sign > 0 else "SELL", ticker, qty // 1000,
                         e["price"] if e else "?", "WON" if won else "lost")
                del self.auction_watch[tid]
                if (not won and self.crowd_learn and self.p.get("crowd_learn_lost_auctions", False)
                        and snap.mid(ticker) is not None):
                    # RIVAL WINNER: a lost auction is a block another desk now holds and must unwind, starting at the
                    # award. Score the price from NOW, in the direction the winner unwinds (-sign: they bought what we
                    # bid to buy, so they sell). A fresh id keeps it out of `taken` (crowd_untaken_only).
                    oid = -int(tid)
                    self.crowd_obs.append((snap.abs_tick, ticker, -sign, qty / 10_000, snap.mid(ticker), oid))
                    self.crowd_sigma[oid] = self.price_vol(ticker, snap)

    def on_stop(self) -> None:
        """HEAT SUMMARY: every tender taken, what it was expected to make and what its block made."""
        if not self.ledger:
            return
        log.info("HEAT SUMMARY (expected = the bot's estimate when it answered; made = its block's P&L when flat)")
        for tid, e in self.ledger.items():
            state = ("lost auction" if e["won"] is False else "won auction" if e["kind"] == "auction" and e["won"]
                     else "auction pending" if e["kind"] == "auction" else "")
            made = f"{e['realized']:+,.0f}" if e["realized"] is not None else (
                "-" if e["won"] is False else "open at the bell")
            log.info("  #%-4s %-4s %-4s %3dk %-7s expected %+8s  made %s %s", tid, e["side"], e["ticker"],
                     e["qty"] // 1000, e["kind"], f"{e['expected']:,.0f}" if e["won"] is not False else "-", made,
                     state)
        live = [e for e in self.ledger.values() if e["won"] is not False]
        auctions = [e for e in self.ledger.values() if e["kind"] == "auction"]
        log.info("  total expected %s, made %s on %d closed blocks; auctions won %d of %d bids",
                 f"{sum(e['expected'] for e in live):+,.0f}",
                 f"{sum(e['realized'] for e in live if e['realized'] is not None):+,.0f}",
                 sum(e["realized"] is not None for e in live), sum(bool(e["won"]) for e in auctions), len(auctions))

    def handle_tenders(self, snap: Snapshot) -> None:
        now = snap.abs_tick
        positions = {t: q + self.pending_delta(t, now, room=True) for t, q in snap.positions.items()}
        tenders = list(self.current_tenders())
        if self.p.get("rank_tenders", False):
            # RE-RANK: several tenders open at once compete for the same net limit, so answer the most valuable
            # first ($ value = profit/share x size), not in arrival order.
            fresh = [t for t in tenders if int(t.get("tender_id", -1)) not in self.seen and t.get("ticker")]
            if len(fresh) > 1:
                val = {id(t): self._evaluate(t, snap, positions, room=10**9).profit_per_share
                       * int(t.get("quantity", 0)) for t in fresh}
                tenders.sort(key=lambda t: -val.get(id(t), float("inf")))
        for t in tenders:
            tid = int(t.get("tender_id", -1))
            ticker = t.get("ticker")
            if tid in self.seen or ticker is None:
                continue
            book = snap.book(ticker)
            if tid not in self.journaled:
                self.journaled.add(tid)
                self.ex.journal.write(
                    "tender_seen", tid=tid, action=t.get("action"), ticker=ticker, qty=t.get("quantity"),
                    price=t.get("price"), fixed=t.get("is_fixed_bid", True), expires=t.get("expires"),
                    caption=t.get("caption"), bid=book.best_bid, ask=book.best_ask, mid=book.mid,
                    bid_depth10=book.depth("bid", (book.best_bid or 0) - 0.10) if book.best_bid else 0,
                    ask_depth10=book.depth("ask", (book.best_ask or 0) + 0.10) if book.best_ask else 0,
                    pos=positions.get(ticker, 0))
            if self.crowd_learn and snap.mid(ticker) is not None and tid not in self.observed:
                self.observed.add(tid)
                # The price runs AGAINST our unwind: down after a BUY tender (we must sell).
                sign = -1 if str(t.get("action", "BUY")).upper() == "BUY" else 1
                self.crowd_obs.append((snap.abs_tick, ticker, sign, int(t.get("quantity", 0)) / 10_000,
                                       snap.mid(ticker), tid))
                self.crowd_sigma[tid] = self.price_vol(ticker, snap)
            if self.wait_to_decide(t, tid, snap):
                continue          # keep the option open: answer `decide_late_ticks` before the window closes
            d = self.decide(t, snap, positions)
            if not d.accept and self.wait_for_room(t, d, snap):
                continue          # room frees up as other blocks unwind / pending auction bids resolve: re-check
            self.seen.add(tid)
            log.info("TENDER %s %s %s x%s @ %s -> %s (%s)%s", tid, t.get("action"), ticker,
                     t.get("quantity"), t.get("price"), "ACCEPT" if d.accept else "decline", d.reason,
                     f" ~ ${d.profit_per_share * float(t.get('quantity') or 0):,.0f} expected" if d.accept else "")
            if d.accept and ticker not in self.pnl_base:
                self.pnl_base[ticker] = (snap.stock_pnl(ticker), tid)     # BLOCK P&L is measured from here
            self.ex.journal.write("tender_decision", tid=tid, accept=d.accept, price=d.price, pps=d.profit_per_share,
                                  exit_vwap=d.unwind_vwap, reason=d.reason, mid=book.mid, bid=book.best_bid,
                                  ask=book.best_ask, ticks_left=snap.ticks_left, expires=t.get("expires"),
                                  pos=positions.get(ticker, 0), dry_run=self.ex.dry_run)
            if self.ex.dry_run:
                continue
            try:
                if d.accept:
                    resp = self.client.accept_tender(tid, None if t.get("is_fixed_bid", True) else d.price)
                    self.ex.journal.write("tender_answer", tid=tid, answer="accept", resp=resp)
                    if not t.get("is_fixed_bid", True) and self.p.get("freeze_rejected_bids", True):
                        # A bid is in the auction until the window closes, whatever the immediate reply says:
                        # the award is only final at expiry, so trading the stock before then is front-running.
                        self.bidding[tid] = (ticker, now + max(0, int(t.get("expires", snap.tick)) - snap.tick))
                    if isinstance(resp, dict) and resp.get("success") is False:
                        log.info("TENDER %s not filled (competitive bid %s rejected)", tid, d.price)
                        continue          # we hold nothing: don't spend risk room on it
                    self.taken.add(tid)
                    sign = 1 if str(t.get("action")).upper() == "BUY" else -1
                    fixed = bool(t.get("is_fixed_bid", True))
                    self.ledger[tid] = {"ticker": ticker, "side": t.get("action"), "qty": int(t["quantity"]),
                                        "kind": "fixed" if fixed else "auction", "price": d.price,
                                        "expected": d.profit_per_share * int(t["quantity"]), "realized": None,
                                        "won": None if not fixed else True}
                    self.block_tids.setdefault(ticker, []).append(tid)
                    if not fixed:
                        self.auction_watch[tid] = [ticker, sign, int(t["quantity"]),
                                                   now + max(0, int(t.get("expires", snap.tick)) - snap.tick), None]
                    if t.get("is_fixed_bid", True) or self.p.get("reserve_auction_room", True):
                        positions[ticker] = positions.get(ticker, 0) + sign * int(t["quantity"])
                    wait = int(self.p.get("booking_ticks", 2))
                    if not t.get("is_fixed_bid", True):    # auctions may only be awarded at the window's end
                        wait += max(0, int(t.get("expires", snap.tick)) - snap.tick)
                    if not t.get("is_fixed_bid", True):
                        self.bidding[tid] = (ticker, now + max(0, int(t.get("expires", snap.tick)) - snap.tick))
                    if self.p.get("track_bookings", True):
                        self.pending[tid] = (ticker, sign * int(t["quantity"]), now + wait)
                elif self.p.get("decline_explicitly", True):
                    self.client.decline_tender(tid)
                    self.ex.journal.write("tender_answer", tid=tid, answer="decline")
            except Exception as exc:       # tender may have expired between read and accept
                log.warning("tender %s action failed: %s", tid, exc)
                self.ex.journal.write("tender_answer", tid=tid, answer="failed", msg=str(exc)[:300])

    def unwind(self, snap: Snapshot) -> None:
        if self.mode == "block":
            self.unwind_block(snap)
        else:
            self.unwind_slices(snap)

    def unwind_block(self, snap: Snapshot) -> None:
        now = snap.abs_tick
        period_end = snap.period * snap.ticks_per_period - int(self.cfg.get("run", {}).get("wind_down_ticks", 5)) - 1
        if self.close_hold:
            # HOLD TO THE BELL (2027 brief): what is still open at the end is closed at the last traded
            # price, with no fine. Crossing a thin book in the final seconds pays fee + spread + impact
            # to avoid a few seconds of random-walk risk, so near the end only rest at the touch.
            self.algo.passive_from = snap.period * snap.ticks_per_period - self.close_hold
        for t in self.tickers:
            if t in self.frozen:
                continue
            pos = snap.positions.get(t, 0)
            target = self.unwind_target(t, pos, now)
            b = self.algo.blocks.get(t)
            if pos == target:
                if b:
                    self.algo.cancel(t)
                if b and pos == 0 and t in self.pnl_base and t in snap.securities:
                    base, tid = self.pnl_base.pop(t)
                    made = snap.stock_pnl(t) - base
                    tids = [x for x in self.block_tids.pop(t, [tid]) if self.ledger.get(x, {}).get("won") is not False]
                    exp = sum(self.ledger[x]["expected"] for x in tids if x in self.ledger)
                    for x in tids:                   # one block can carry several tenders: split by expected $
                        if x in self.ledger:
                            e = self.ledger[x]["expected"]
                            self.ledger[x]["realized"] = made * (e / exp if exp else 1 / len(tids))
                    log.info("BLOCK P&L %s: %s since tender %s was accepted (flat now; expected %s)", t,
                             f"{made:+,.0f}", " + ".join(f"#{x}" for x in tids), f"{exp:+,.0f}")
                continue
            # New / grown / flipped exposure (e.g. another tender accepted): restart the schedule.
            if (b is None or b.target != target or (pos > 0) != (b.start_pos > 0)
                    or abs(pos) > abs(b.start_pos)):
                kappa = 0.0
                if self.schedule == "almgren_chriss":
                    # sigma: GARCH $/share/tick; eta: from the side we unwind INTO.
                    kappa = ac_kappa(self.price_vol(t, snap), book_eta(snap.book(t), "bid" if pos > 0 else "ask"),
                                     self.ac_lambda)
                self.algo.work(t, pos, target, now, min(now + self.unwind_horizon(), period_end), kappa=kappa)
        if self.stuck_window:
            for t in list(self.unwind_hist):
                if t not in self.algo.blocks:
                    del self.unwind_hist[t]
            for t, b in self.algo.blocks.items():
                h = self.unwind_hist.setdefault(t, [])
                if h and h[0][0] < b.start_tick:          # a new schedule (block restarted): new history
                    h.clear()
                rem = abs(snap.positions.get(t, 0) - b.target)
                if h and h[-1][0] == now:                  # several loops per tick: keep one sample per tick
                    h[-1] = (now, rem)
                else:
                    h.append((now, rem))
                del h[:-(self.stuck_window + 20)]
        trend_z = float(self.cfg.get("execution", {}).get("trend_unwind_z", 0.0))
        if self.hold_budget and snap.ticks_left <= self.hold_until:
            self.algo.passive = set()              # flat by the bell: the schedule crosses what is left
        elif self.hold_budget:
            self.algo.passive = {t for t in self.algo.blocks
                                 if hold_within_budget(snap.positions.get(t, 0) - self.algo.blocks[t].target,
                                                       self.price_vol(t, snap), snap.ticks_left, self.hold_budget)
                                 and not (self.stuck_window and unwind_stuck(self.unwind_hist.get(t, []), now,
                                                                             self.stuck_window, self.stuck_horizon))
                                 and not (trend_z and self.trend_against(t, snap.positions.get(t, 0), snap) >= trend_z)}
        if self.algo.active and self.fresh_unwind and not self.ex.dry_run:
            def refresh():
                orders = self.client.orders("OPEN")         # orders FIRST, then positions (see step_fresh)
                return orders, self.client.positions()
            self.algo.step_fresh({t: snap.book(t) for t in self.algo.blocks}, snap.positions, now, refresh,
                                 lambda t, pos: self.unwind_target(t, pos, now))
        elif self.algo.active:
            open_ids = None if self.ex.dry_run else {int(o["order_id"]) for o in self.client.orders("OPEN")}
            self.algo.step({t: snap.book(t) for t in self.algo.blocks}, snap.positions, now, open_ids)

    def unwind_slices(self, snap: Snapshot) -> None:
        urgency = self.p.get("urgent_ticks", 30)
        for ticker in self.tickers:
            if ticker in self.frozen:
                continue
            pos = snap.positions.get(ticker, 0)
            pos -= self.unwind_target(ticker, pos, snap.abs_tick)
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
        self.sync_tickers(snap)
        if self.close_hold:
            # Hold to the bell: keep resting at the touch, never cross; the case closes the rest at the last price.
            self.frozen = self.undecided(snap) if self.p.get("respect_windows", False) else set()
            for t in self.frozen:
                if t in self.algo.blocks:
                    self.algo.cancel(t)
            self.unwind(snap)
            return
        # Last ticks: dump what is left - an unclosed position is pure risk.
        for t in list(self.algo.blocks):
            self.algo.cancel(t)
        self.ex.cancel_all()
        for ticker in self.tickers:
            pos = snap.positions.get(ticker, 0)
            pos -= self.unwind_target(ticker, pos, snap.abs_tick)
            if pos:
                book = snap.book(ticker)
                touch = book.best_bid if pos > 0 else book.best_ask
                if touch:
                    px = touch - 0.25 if pos > 0 else touch + 0.25
                    self.ex.limit(ticker, "SELL" if pos > 0 else "BUY", abs(pos), px)
