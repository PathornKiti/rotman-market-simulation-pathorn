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

from .. import mlmodel
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
    offset_credit: float = 0.0,
    drift_horizon_ticks: float = 0.0,
) -> TenderDecision:
    """
    Pure decision function. `tender['action']` is OUR side of the trade:
    BUY means we buy the block from the client (and must later SELL it).
    `drift_per_tick` is the recent mid drift (+ = rising); it is a cost when it
    runs against our unwind direction over the expected unwind time.
    `flow_rate` > 0 (FORWARD LIQUIDITY, shares/tick the market trades on our unwind side): we unwind by resting
    at the touch, so the exit is the touch we rest at, and the time it takes is to_unwind / flow_rate (the risk
    premium and drift grow with it). 0 = the old static model: walk the visible book x `refill_factor`.
    `offset_credit` ($/share): what unwinding the position we already hold is expected to cost; the part of the
    tender that offsets that position saves it, so that part is worth this much more (NETTING).
    `drift_horizon_ticks` > 0: charge the drift over at most this many ticks of the unwind (a trend fades).
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
    drift_ticks = min(unwind_ticks, drift_horizon_ticks) if drift_horizon_ticks > 0 else unwind_ticks
    drift_cost = (-sign * drift_per_tick if drift_symmetric else max(0.0, -sign * drift_per_tick)) * drift_ticks
    drift_cost += risk_aversion * price_vol_per_tick * unwind_ticks ** 0.5
    drift_cost += crowd_cost             # learned: how far the price runs against tenders' unwinds
    fees = fee * (to_unwind / qty)       # we pay the taker fee only on what hits the book
    fees -= offset_credit * offset / qty  # NETTING: the offset part spares our open block its unwind cost

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


def solve_linear(a: list[list[float]], v: list[float]) -> list[float] | None:
    """Solve a x = v (Gauss-Jordan, tiny systems); None if singular."""
    k = len(v)
    a, v = [row[:] for row in a], v[:]
    for c in range(k):
        if abs(a[c][c]) < 1e-15:
            return None
        piv = a[c][c]
        a[c] = [x / piv for x in a[c]]
        v[c] /= piv
        for r in range(k):
            if r != c:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
                v[r] -= f * v[c]
    return v


def cross_cost(book: OrderBook, action: str, qty: int, cap: float = 0.30) -> float:
    """$/share vs the mid to cross `qty` now (BUY lifts asks, SELL hits bids); `cap` if the book cannot take it."""
    mid = book.mid
    if not mid or qty <= 0:
        return cap
    f = book.walk(action, qty)
    if not f.complete:
        return cap
    return (f.vwap - mid) if action.upper() == "BUY" else (mid - f.vwap)


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
        self.drift: dict[str, EWMA] = {t: self.new_drift() for t in self.tickers}
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
        self.sprint_walk = float(ex_cfg.get("sprint_max_walk", 0.0))   # $ a sprint may move from its start mid
        self.sprint_anchor: dict[tuple[str, int], float] = {}   # (ticker, block start tick) -> mid at its first sprint
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
        self.absorb: dict[str, float] = {}       # ticker -> learned shares/tick of our own unwinds
        self.exec_cost: float | None = None       # learned $/share an unwind costs vs the mid at acceptance
        self.mid_hist: dict[str, list[float]] = {}   # TREND: one mid per tick per stock (last 60)
        self.vol_hist: dict[str, list[float]] = {}   # ML: cumulative traded volume per tick per stock (last 60)
        self.ml_pred: dict[str, float] = {}          # ML: this tick's predicted 10-tick move per stock
        self.front_seen: set[str] = set()            # FRONT-LOAD: blocks still crossing their first slice
        self.front_sent0: dict[tuple[str, int], int] = {}   # FRONT-LOAD: (ticker, block start) -> shares sent before
        # ML SPRINT (`sprint_model`, 2026-10-15, practice only): the trained 10-tick price model (ritc.mlmodel) gives
        # the SPRINT rule's expected move against the block instead of the run + book model. Off when unset.
        self.ml_model = mlmodel.load(s["sprint_model"], s.get("sprint_model_key", "price_trees")) \
            if s.get("sprint_model") else None
        if self.ml_model:
            log.info("ML SPRINT model: %s (%s, %d trees)", s["sprint_model"], s.get("sprint_model_key", "price_trees"),
                     len(self.ml_model.get("trees", [])))
        self.own_flow: dict[str, list[float]] = {}   # signed shares WE crossed per tick per stock (aligned to mid_hist)
        self.own_seen = 0                             # tca.fills already counted into own_flow
        # BOOK SIGNAL: features (run, asym, ofi) at the last window per stock, and the sums of x x', x y, y y, n over
        # (features at a window start -> the next window's move), every stock pooled
        self.sig_prev: dict[str, tuple[float, ...]] = {}
        self.sig_xx = [[0.0] * 3 for _ in range(3)]
        self.sig_xy = [0.0] * 3
        self.sig_yy = 0.0
        self.sig_n = 0
        self.ofi_tick: dict[str, list[float]] = {}   # OFI: Cont-Kukanov-Stoikov events summed per tick (last 60)
        self.ofi_last: dict[str, tuple[float, int, float, int]] = {}   # previous top of book (bid, size, ask, size)
        self.ofi_seen: dict[str, int] = {}
        self.l1_scale: dict[str, float] = {}         # EWMA of the top-of-book size (bid + ask), to scale OFI
        self.mom = [0, 0.0, 0.0, 0.0]                  # TREND: n, sum ab, sum aa, sum bb of (last 10, next 10) moves
        self.hold_tests: dict[str, dict] = {}     # HOLD TEST (practice): ticker -> window being held untouched
        self.hold_tests_done = 0
        self.hold_test_log: list[dict] = []
        self.hold_stats = [0, 0.0, 0.0]          # HOLDER DRIFT: n, sum, sum of squares of the mid's 1-tick move against
        self.last_pos: dict[str, int] = {}        # the position we held over that tick
        self.block_info: dict[str, dict] = {}   # ticker -> how its current block started (BLOCK DONE stats)
        self.blocks_done: list[dict] = []
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
        self.answers: dict[int, dict] = {}              # tender id -> how it was answered (the HEAT REPORT)
        self.seen_at: dict[int, int] = {}               # tender id -> abs tick it was first listed
        # FORWARD LIQUIDITY (docs/PRACTICE_LOGS.md): shares/tick the market trades on each side of each stock, from
        # time & sales, as an EWMA started at the practice-measured absorption of our resting unwinds. "SELL" =
        # buyer-initiated volume (it fills a resting sell). Used to price tenders (`liquidity_model = "flow"`).
        self.liq_model = s.get("liquidity_model", "book")
        self.liq_prior: dict[str, float] = dict(s.get("liq_prior", {}))
        self.liq: dict[tuple[str, str], float] = {}
        self.tas_after: dict[str, int] = {}
        self.liq_tick: dict[str, int] = {}

    def new_drift(self) -> EWMA:
        """
        Drift estimate (EWMA of 1-tick mid changes). `drift_zero_start` (2026-10-15): start the mean at 0 instead of the
        heat's FIRST 1-tick change, which otherwise still carries ~30% of the weight at the first tender (tick ~27).
        Valuation charges only adverse drift, so that one random tick made early tenders one-sidedly pessimistic, and
        the same tender's value depended on the tick the bot started on (live vs replay: +0.199 vs -0.032).
        """
        e = EWMA(self.p.get("drift_halflife", 15))
        if self.p.get("drift_zero_start", False):
            e.mean = 0.0
        return e

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
            self.drift[t] = self.new_drift()
            self.garch[t] = OnlineGarch()
        log.info("tickers: %s", ", ".join(tickers))
        self.tickers = tickers

    def _update_drift(self, snap: Snapshot) -> None:
        if snap.tick == self.last_tick:          # one time-series observation per tick
            return
        self.last_tick = snap.tick
        if self.crowd_learn:
            self._learn_crowd(snap)
        own: dict[str, float] = {}
        fills = self.ex.tca.fills
        for f in fills[self.own_seen:]:
            if f.style == "aggressive":
                own[f.ticker] = own.get(f.ticker, 0.0) + (f.qty if f.action.upper() == "BUY" else -f.qty)
        self.own_seen = len(fills)
        for t in self.tickers:
            m = snap.mid(t)
            if m is None:
                continue
            self.garch[t].add_price(m)
            if t in self.last_mid:
                self.drift[t].update(m - self.last_mid[t])
                q = self.last_pos.get(t, 0)
                if q:
                    x = (-1 if q > 0 else 1) * (m - self.last_mid[t])     # + = the mid moved against what we held
                    self.hold_stats[0] += 1
                    self.hold_stats[1] += x
                    self.hold_stats[2] += x * x
            self.last_mid[t] = m
            h = self.mid_hist.setdefault(t, [])
            h.append(m)
            del h[:-60]
            v = self.vol_hist.setdefault(t, [])
            v.append(float((snap.securities.get(t) or {}).get("volume") or 0.0))
            del v[:-60]
            if self.ml_model is not None:          # once per tick, at its first loop: the moment training data uses
                x = self.ml_move(t, snap)
                if x is None:
                    self.ml_pred.pop(t, None)
                else:
                    self.ml_pred[t] = x
            o = self.own_flow.setdefault(t, [])
            o.append(own.get(t, 0.0))
            del o[:-60]
            w = int(self.p.get("trend_window", 10))
            if len(h) > 2 * w and snap.tick % w == 0:       # non-overlapping (previous w, next w) pairs
                a, b = h[-1 - w] - h[-1 - 2 * w], h[-1] - h[-1 - w]
                self.mom[0] += 1
                self.mom[1] += a * b
                self.mom[2] += a * a
                self.mom[3] += b * b
            if len(h) > w and snap.tick % w == 0 and self.p.get("book_signal", False):
                run = h[-1] - h[-1 - w]
                if t in self.sig_prev:                       # the previous window's features -> this window's move
                    x = self.sig_prev[t]
                    for i in range(3):
                        self.sig_xy[i] += x[i] * run
                        for j in range(3):
                            self.sig_xx[i][j] += x[i] * x[j]
                    self.sig_yy += run * run
                    self.sig_n += 1
                self.sig_prev[t] = self.features(t, snap)
        self.last_pos = dict(snap.positions)
        if self.liq_model in ("flow", "auctions", "adaptive", "flow_guarded"):
            self._update_liquidity(snap)
        if snap.tick % 60 == 0:      # practice check: compare with `calibrate`'s measured sigma/tick per stock
            log.info("VOL " + "  ".join(f"{t} ${self.price_vol(t, snap):.4f}/tick" for t in self.tickers))
            if self.liq_model in ("flow", "auctions", "adaptive", "flow_guarded"):
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
        if self.liq_model not in ("flow", "auctions", "adaptive", "flow_guarded"):
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

    def trend_r(self) -> float:
        """
        TREND (root cause, practice 2026-10-13 18:37): the mid's move over the last `trend_window` ticks predicts the
        next one (r +0.46, t 5.0, variance ratio 1.5-2.2, while we held nothing). Learned live from this heat's own
        prices (every stock, non-overlapping pairs), shrunk to the prior `trend_prior_r` with weight `trend_prior_n`
        pairs, so a random-walk market drifts it to ~0 within the heat. 0 when `trend_learn` is off.
        """
        if not self.p.get("trend_learn", False):
            return 0.0
        n, sab, saa, sbb = self.mom
        r = sab / (saa * sbb) ** 0.5 if n >= 3 and saa > 0 and sbb > 0 else 0.0
        n0, r0 = float(self.p.get("trend_prior_n", 20)), float(self.p.get("trend_prior_r", 0.46))
        return max(0.0, (n * r + n0 * r0) / (n + n0))

    def run_against(self, ticker: str, held: int) -> float:
        """$ the mid moved over the last `trend_window` ticks AGAINST a position of sign `held` (+ = against)."""
        if not held:
            return 0.0
        return (-1 if held > 0 else 1) * self.market_run(ticker)

    def market_run(self, ticker: str) -> float:
        """
        $ the mid moved over the last `trend_window` ticks. `run_ex_own`: minus what OUR crossing moved it
        (`own_impact` $/share x the shares we crossed in the window). Practice 2026-10-14: while we held a block
        and did not sprint, a 10-tick run continued (r +0.43, t 4.0); during our own sprints it reversed
        (r -0.33): the sprint read its own impact as a trend against the block and kept crossing (CROC 59.5k
        walked 20.29 -> 21.54).
        """
        h, w = self.mid_hist.get(ticker, []), int(self.p.get("trend_window", 10))
        if len(h) <= w:
            return 0.0
        run = h[-1] - h[-1 - w]
        if self.p.get("run_ex_own", False):
            lam = float(self.p.get("own_impact", {}).get(ticker, 0.0))
            run -= lam * sum(self.own_flow.get(ticker, [])[-w:])
        return run

    def book_asym(self, ticker: str, snap: Snapshot) -> float:
        """
        DEPTH ASYMMETRY ($): cost to cross `book_signal_shares` on the ask side minus the bid side (others' orders).
        + = the asks are thin, so the price is likely to rise through them. Root-cause recordings 2026-10-13: after
        momentum it predicts the next 10 ticks with r +0.37 (t 7.6), in every heat and stock.
        """
        b = snap.book(ticker)
        n = int(self.p.get("book_signal_shares", 25000))
        return cross_cost(b, "BUY", n) - cross_cost(b, "SELL", n)

    def _update_ofi(self, snap: Snapshot) -> None:
        """
        ORDER FLOW IMBALANCE (Cont, Kukanov & Stoikov 2014) from consecutive top-of-book snapshots, others' orders only:
        e = q_bid 1{bid >= old} - old q_bid 1{bid <= old} - q_ask 1{ask <= old} + old q_ask 1{ask >= old}. Summed per
        tick; scaled by an EWMA of the top-of-book size. Root-cause recordings 2026-10-13: OFI over the last 10 ticks
        predicts the next 10 beyond run + depth asymmetry (r +0.21, t 4.2; every heat t 2.3-2.5).
        """
        if not self.p.get("book_signal", False):
            return
        for t in self.tickers:
            b = snap.book(t)
            if not b.bids or not b.asks:
                continue
            cur = (b.bids[0].price, b.bids[0].quantity, b.asks[0].price, b.asks[0].quantity)
            ticks = self.ofi_tick.setdefault(t, [])
            if self.ofi_seen.get(t) != snap.abs_tick:
                ticks.append(0.0)
                del ticks[:-60]
                self.ofi_seen[t] = snap.abs_tick
                sc = self.l1_scale.get(t)
                self.l1_scale[t] = cur[1] + cur[3] if sc is None else 0.95 * sc + 0.05 * (cur[1] + cur[3])
            old = self.ofi_last.get(t)
            if old:
                pb, qb, pa, qa = cur
                ob, oqb, oa, oqa = old
                ticks[-1] += ((qb if pb >= ob else 0) - (oqb if pb <= ob else 0)
                              - (qa if pa <= oa else 0) + (oqa if pa >= oa else 0))
            self.ofi_last[t] = cur

    def ofi(self, ticker: str) -> float:
        """OFI over the last `trend_window` ticks / the stock's typical top-of-book size (+ = buying pressure)."""
        w = int(self.p.get("trend_window", 10))
        return sum(self.ofi_tick.get(ticker, [])[-w:]) / max(1.0, self.l1_scale.get(ticker, 1.0))

    def features(self, ticker: str, snap: Snapshot) -> tuple[float, float, float]:
        """(last run $, depth asymmetry $, OFI) now, the book signal's inputs."""
        return self.market_run(ticker), self.book_asym(ticker, snap), self.ofi(ticker)

    # models tried richest first; priors = the 3 recorded heats' fits ($ next 10-tick move per unit of each feature)
    BOOK_MODELS = (((2, 1), (0.0171, 0.6718)), ((0, 1), (0.40, 0.67)))
    FEATURE_VAR = (0.01715, 0.00631, 12.95)          # E[x^2] of run, asym, ofi in the recordings (prior weights)

    def _solve(self, idx: tuple[int, ...], prior: tuple[float, ...] | None) -> list[float] | None:
        """OLS of the next run on features `idx` (no intercept); with `prior`, shrunk to it with book_prior_n
        pseudo-windows of the recorded feature variances."""
        n0 = float(self.p.get("book_prior_n", 20)) if prior else 0.0
        a = [[self.sig_xx[i][j] + (n0 * self.FEATURE_VAR[i] if i == j else 0.0) for j in idx] for i in idx]
        v = [self.sig_xy[i] + (n0 * self.FEATURE_VAR[i] * prior[m] if prior else 0.0) for m, i in enumerate(idx)]
        return solve_linear(a, v)

    def _t_stats(self, idx: tuple[int, ...]) -> list[float] | None:
        """t of each OLS weight from this heat's windows alone (>= 10 windows)."""
        beta = self._solve(idx, None)
        if beta is None or self.sig_n < 10:
            return None
        rss = self.sig_yy - sum(b * self.sig_xy[i] for b, i in zip(beta, idx))
        s2 = max(rss, 1e-12) / max(1, self.sig_n - len(idx))
        xx = [[self.sig_xx[i][j] for j in idx] for i in idx]
        out = []
        for m in range(len(idx)):                            # diagonal of (X'X)^-1
            inv = solve_linear(xx, [1.0 if r == m else 0.0 for r in range(len(idx))])
            if inv is None:
                return None
            out.append(beta[m] / max(s2 * inv[m], 1e-18) ** 0.5)
        return out

    def book_model(self) -> tuple[tuple[int, ...], list[float]] | None:
        """
        The richest book model THIS heat supports: OFI + depth asymmetry, else run + depth asymmetry, each used only
        once every book feature in it has a positive live t >= `book_gate_t` (>= 10 windows); None = momentum only.
        `book_models = "ofi"` tries the OFI model first; "asym" (default) skips it. Real leave-one-heat-out backtest
        (2026-10-13), $/heat of 10k sprint decisions: momentum 8.5k, + asymmetry 15.4k, + OFI 20.7k, OFI + asym 23.1k.
        """
        if not self.p.get("book_signal", False):
            return None
        g = float(self.p.get("book_gate_t", 0))
        models = self.BOOK_MODELS if self.p.get("book_models", "asym") == "ofi" else self.BOOK_MODELS[1:]
        for idx, prior in models:
            if g > 0:
                ts = self._t_stats(idx)
                if ts is None or any(ts[m] < g for m, i in enumerate(idx) if i != 0):
                    continue
            beta = self._solve(idx, prior)
            if beta is not None:
                return idx, [max(0.0, b) for b in beta]
        return None

    def book_beta(self) -> tuple[float, float]:
        """(a, b) of the run + asymmetry model, shrunk to the real fit (kept for reports and tests)."""
        beta = self._solve((0, 1), self.BOOK_MODELS[1][1]) or list(self.BOOK_MODELS[1][1])
        return max(0.0, beta[0]), max(0.0, beta[1])

    def book_signal_on(self) -> bool:
        return self.book_model() is not None

    def ml_move(self, ticker: str, snap: Snapshot) -> float | None:
        """ML SPRINT: the model's expected mid move over the next 10 ticks ($); None until 21 ticks of history."""
        bid, ask = snap.quote(ticker)
        f = mlmodel.price_features(self.mid_hist.get(ticker, []), self.vol_hist.get(ticker, []), snap.book(ticker),
                                   (ask - bid) if bid and ask else 0.0, snap.tick, ticker)
        return mlmodel.predict(self.ml_model, f) if f else None

    def predicted_against(self, ticker: str, held: int, snap: Snapshot) -> float:
        """$ the next window is expected to move against a position of sign `held` (ML, book model or momentum)."""
        if not held:
            return 0.0
        if self.ml_model is not None and ticker in self.ml_pred:
            return (-1 if held > 0 else 1) * self.ml_pred[ticker]
        m = self.book_model()
        if m is not None:
            idx, beta = m
            x = self.features(ticker, snap)
            return (-1 if held > 0 else 1) * sum(b * x[i] for b, i in zip(beta, idx))
        return self.trend_r() * self.run_against(ticker, held)

    def cross_threshold(self, ticker: str, held: int, snap: Snapshot) -> float:
        """$ / share a sprint must save: `live_cross_cost` = what crossing the next slice costs in the book now plus
        the fee (deep book: cheap, thin book: dear); otherwise the fixed `trend_cross_cost`."""
        if not self.cfg.get("execution", {}).get("live_cross_cost", False):
            return float(self.p.get("trend_cross_cost", 0.03))
        n = min(abs(held), int(self.cfg.get("execution", {}).get("live_cross_shares", 10000)))
        return max(0.01, cross_cost(snap.book(ticker), "SELL" if held > 0 else "BUY", n)
                   + float(self.fees.get(ticker, 0.02)))

    def trend_cost(self, ticker: str, held: int) -> float:
        """Expected further move against a position of sign `held` over the next window: r x the last run
        (negative = a credit, the run is in our favour), capped at +-`trend_value_cap`."""
        if not self.p.get("trend_value", False):
            return 0.0
        cap = float(self.p.get("trend_value_cap", 0.15))
        return max(-cap, min(cap, self.trend_r() * self.run_against(ticker, held)))

    def holder_drift(self) -> float:
        """
        HOLDER DRIFT ($/tick), learned live (`holder_drift_learn`). Solo practice 2026-10-13: while we held a block the
        mid moved $0.15 per 30 s against it (t 3.2) whatever we traded, and 0.000 while nobody held; that made holding,
        not trading, the cost, and the random-walk logic (hold, rest, never cross) the wrong one. Mean 1-tick move
        against our position over every tick we held anything; 0 until it is `holder_z` standard errors above zero
        over at least `holder_min_ticks` ticks, so a random-walk market never switches it on.
        """
        if not self.p.get("holder_drift_learn", False):
            return 0.0
        n, sm, sq = self.hold_stats
        if n < int(self.p.get("holder_min_ticks", 20)):
            return 0.0
        mean = sm / n
        sd = max((sq / n - mean * mean), 1e-12) ** 0.5
        if mean <= float(self.p.get("holder_z", 2.5)) * sd / n ** 0.5:
            return 0.0
        return min(mean, float(self.p.get("holder_cap", 0.03)))

    def holder_cost(self, ticker: str, qty: int) -> float:
        """$/share the holder drift costs a new block: drift x half the ticks it takes to unwind (linear unwind)."""
        d = self.holder_drift()
        return d * self.unwind_horizon(ticker, qty) / 2 if d and qty else 0.0

    def netting_credit(self) -> float:
        """`netting_credit`: $/share credited to the part of a tender that offsets our open block. `netting_gate`: only
        while no rival desk has shown (solo market: +$4.2k/heat; with rivals the same credit cost $5-7k/heat)."""
        if not self.p.get("netting_credit", False):
            return 0.0
        gate = self.p.get("netting_gate", False)
        if (gate == "crowded" and self.crowded()) or (gate is True and not self.no_crowd_seen()):
            return 0.0
        return self.unwind_cost_ps()

    def unwind_cost_ps(self) -> float:
        """Learned $/share our unwinds cost vs the mid at acceptance (prior `netting_prior`; solo practice 0.21)."""
        v = self.exec_cost if self.exec_cost is not None else float(self.p.get("netting_prior", 0.15))
        return min(max(v, 0.0), float(self.p.get("netting_cap", 0.40)))

    def absorption(self, ticker: str) -> float:
        """Shares/tick our own unwinds of this stock achieved: EWMA over finished blocks, from the practice prior."""
        prior = float(self.liq_prior.get(ticker, self.p.get("liq_default", 2000)))
        return max(100.0, self.absorb.get(ticker, prior))

    def unwind_horizon(self, ticker: str | None = None, qty: int = 0) -> int:
        """
        Race the crowd: once it is known to unwind into our book, finish before it does.
        `horizon_mode = "absorption"` (ADAPTIVE): the target is qty / the absorption rate this stock has shown, clipped
        to [horizon_min_ticks, horizon_max_ticks], instead of a fixed `unwind_horizon_ticks`: a thin book gets the time
        it needs (forcing it pays impact), a deep one is done sooner (less risk, net room back sooner).
        """
        ex = self.cfg.get("execution", {})
        h = self.horizon
        if ex.get("horizon_mode", "fixed") == "absorption" and ticker and qty:
            h = int(min(max(qty / self.absorption(ticker), ex.get("horizon_min_ticks", 15)),
                        ex.get("horizon_max_ticks", 90)))
        fast = int(ex.get("crowd_horizon_ticks", 0))
        if self.holder_drift():
            h = min(h, int(self.p.get("holder_horizon_ticks", 20)))     # holding costs: race it
        return min(h, fast) if fast and self.crowded() else h

    def step(self, snap: Snapshot) -> None:
        self.sync_tickers(snap)
        self._update_ofi(snap)
        self._update_drift(snap)
        self._watch_auctions(snap)
        self.handle_tenders(snap)
        self.frozen = self.frozen_now(snap)
        for t in self.frozen:
            if t in self.algo.blocks:
                self.algo.cancel(t)                  # pull resting orders: a fill now is front-running
        self.unwind(snap)

    def maybe_hold_test(self, ticker: str, sign: int, qty: int, fixed: bool, book, snap: Snapshot, now: int) -> None:
        """
        HOLD TEST (practice only, `execution.hold_test_ticks` > 0): after a fixed tender in a stock we held none of,
        do not trade it at all for that many ticks, then unwind as usual. Root-cause experiment (2026-10-13): with
        zero fills, a drift against us means HOLDING moves the price; none means it was our own fills (the market
        maker's inventory) or chance. At most `hold_test_max` per heat; logs one HOLD TEST line each.
        """
        ex = self.cfg.get("execution", {})
        n = int(ex.get("hold_test_ticks", 0))
        if (not n or not fixed or snap.positions.get(ticker, 0) or ticker in self.hold_tests
                or self.hold_tests_done >= int(ex.get("hold_test_max", 2)) or snap.ticks_left < n + 60 or not book.mid):
            return
        self.hold_tests[ticker] = {"until": now + n, "start": now, "mid0": book.mid, "sign": sign, "qty": qty}
        self.hold_tests_done += 1
        log.info("HOLD TEST %s: holding %+d for %d ticks with no trading (mid %.2f)", ticker, sign * qty, n, book.mid)

    def _end_hold_tests(self, snap: Snapshot) -> set[str]:
        """Tickers still in a hold test (frozen); log each test that ends."""
        now = snap.abs_tick
        for t, h in list(self.hold_tests.items()):
            if now < h["until"]:
                continue
            m = snap.mid(t)
            if m:
                move = -h["sign"] * (m - h["mid0"])          # + = against what we held
                rec = {"ticker": t, "ticks": now - h["start"], "qty": h["qty"], "against": round(move, 3),
                       "position": snap.positions.get(t, 0), "vol": round(self.price_vol(t, snap), 4)}
                self.hold_test_log.append(rec)
                self.ex.journal.write("hold_test", **rec)
                log.info("HOLD TEST DONE %s: held %+d for %d ticks, no trading: mid moved %+.3f AGAINST us "
                         "(1 sd of noise over that time ~%.3f)", t, snap.positions.get(t, 0), rec["ticks"], move,
                         rec["vol"] * rec["ticks"] ** 0.5)
            del self.hold_tests[t]
        return set(self.hold_tests)

    def frozen_now(self, snap: Snapshot) -> set[str]:
        """Stocks not to trade this loop (respect_windows). `reduce_in_windows`: the brief says "only trades that REDUCE
        an existing position from an accepted tender are permitted without penalty", and the unwinder never opens a
        position, so a stock we still hold keeps unwinding through an open window."""
        out = self.undecided(snap) if self.p.get("respect_windows", False) else set()
        if self.p.get("reduce_in_windows", False):
            out = {t for t in out if not snap.positions.get(t, 0)}
        return out | (self._end_hold_tests(snap) if self.hold_tests else set())

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
        if not t.get("is_fixed_bid", True) and not self.p.get("auction_bid", True):
            # NO AUCTIONS (practice: won 2 of 76 real bids, 0 of 32 on 2026-10-14). A bid holds ~55k of the 100k
            # net room and freezes its stock for its 30-tick window; 6 of 10 late fixed answers on 2026-10-14
            # waited on a bid that then lost, while the price ran up to 0.385 against the tender.
            return TenderDecision(False, None, 0.0, 0.0, "auctions off (auction_bid = false)")
        skip = float(self.p.get("auction_skip_adding", 0))
        if skip and not t.get("is_fixed_bid", True):
            # AUCTION FREEZE (practice 2026-10-13, -$15k): an open auction bid freezes its stock for the whole
            # window (front-running rule), so bidding one that would ADD to a block we are still unwinding stops
            # that unwind for ~30 s while the price can run, and a win only makes the block bigger. Skip it.
            # `auction_skip_side = "any"` (solo heats 2026-10-13: an OPPOSITE-side bid froze a short CRZY block for
            # 35 s while CRZY ran 11.07 -> 11.46): skip every auction on a stock we are still unwinding.
            sign = 1 if str(t.get("action", "BUY")).upper() == "BUY" else -1
            pos = positions.get(t.get("ticker"), 0)
            side_any = self.p.get("auction_skip_side", "adding") == "any"
            if (abs(pos) if side_any else sign * pos) >= skip:
                return TenderDecision(False, None, 0.0, 0.0, f"auction while we unwind our {pos:+d} block")
        if self.liq_model == "ensemble":
            return self.decide_ensemble(t, snap, positions)
        return self._evaluate(t, snap, positions)

    def flow_pricing(self, t: dict, positions: dict[str, int]) -> bool:
        """Price this tender's exit off forward liquidity (resting at the touch) rather than the visible book walk?"""
        m = self.liq_model
        if m == "flow_guarded":
            # FLOW GUARDED (2026-10-15): forward liquidity unless the crowd alarm is on (crowded(): impact > 2 sd).
            # "adaptive" waits for evidence of NO crowd before trusting flow. Real Rotman heats paid +$4.4k/heat for
            # flow (t 2.6, 43 heats) while the simulator's rival markets lost with it, which is when crowded() fires.
            return not self.crowded()
        return (m == "flow" or (m == "auctions" and self.bid_up(t, positions))
                or (m == "adaptive" and self.no_crowd_seen()))

    def _evaluate(self, t: dict, snap: Snapshot, positions: dict[str, int], flow: bool | None = None,
                  crowd: bool = True, room: int | None = None) -> TenderDecision:
        """evaluate_tender with the bot's state. `flow`: force the forward-liquidity exit on / off (None = the
        liquidity_model's choice); `crowd`: include the learned crowd charge; `room`: override the risk room."""
        ticker = t.get("ticker")
        use_flow = self.flow_pricing(t, positions) if flow is None else flow
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
            # LIQUIDITY-AWARE: time to unwind = shares / the absorption this stock has shown (feeds sigma x sqrt(T))
            unwind_ticks_per_lot=(1.0 / self.absorption(ticker) if self.p.get("liquidity_aware", False)
                                  else self.p.get("unwind_ticks_per_share", 0.0)),
            min_ticks_to_unwind=self.p.get("min_ticks_to_unwind", 10),
            price_vol_per_tick=self.price_vol(ticker, snap),
            risk_aversion=self.p.get("risk_aversion", 0.0),
            crowd_cost=((self.crowd_cost(int(t.get("quantity", 0))) if crowd else 0.0)
                        + self.holder_cost(ticker, int(t.get("quantity", 0)))
                        + self.trend_cost(ticker, 1 if str(t.get("action", "BUY")).upper() == "BUY" else -1)),
            close_hold_ticks=(self.close_hold if self.p.get("hold_valuation", False) and not self.holder_drift()
                              else 0),     # holding to the bell is not free once holding has a drift
            flow_rate=(self.flow(ticker, "SELL" if str(t.get("action", "BUY")).upper() == "BUY" else "BUY")
                       if use_flow else 0.0),
            drift_symmetric=bool(self.p.get("drift_symmetric", False)),
            offset_credit=self.netting_credit(),
            # DRIFT HORIZON (practice 2026-10-14, 47 fixed tenders): the EWMA drift x 20 ticks matched the next 20
            # ticks' move against the unwind (charged 0.118, moved 0.116), but x the full unwind time it charged
            # 0.232 for a 0.078 move over 40 ticks: the trend fades within 10-20 ticks.
            drift_horizon_ticks=float(self.p.get("drift_horizon_ticks", 0)),
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

    def _block_done(self, t: str, snap: Snapshot, made: float, exp: float, tids: list) -> None:
        """
        BLOCK DONE: how the unwind went, for analysis (practice: slow absorption and adverse moves came together).
        secs and shares/s absorbed; mid move over the unwind (+ = in our favour); shares rested vs crossed and their
        average price vs the mid when each order went out (+ = earned).
        """
        info = self.block_info.pop(t, None)
        if not info:
            return
        secs = max(1, snap.abs_tick - info["start"])
        sign = 1 if info["side"] == "SELL" else -1         # SELL unwind: a rising mid is in our favour
        m0, m1 = info["mid0"], snap.mid(t)
        move = sign * (m1 - m0) if m0 and m1 else 0.0
        fills = [f for f in self.ex.tca.fills[info["fill0"]:] if f.ticker == t]
        rest = sum(f.qty for f in fills if f.style == "passive")
        cross = sum(f.qty for f in fills if f.style != "passive")
        edge = (sum(-f.shortfall * f.qty for f in fills) / max(1, rest + cross)) if fills else 0.0
        rec = {"ticker": t, "side": info["side"], "qty": info["qty"], "secs": secs,
               "rate": round(info["qty"] / secs), "mid_move": round(move, 3), "rested": rest, "crossed": cross,
               "fill_edge": round(edge, 4), "made": round(made), "expected": round(exp), "tenders": tids,
               "start_tick": info["start"], "vol": round(self.price_vol(t, snap), 4)}
        self.blocks_done.append(rec)
        a = float(self.p.get("absorb_learn", 0.5))     # learn fast: only a few blocks per stock per heat
        self.absorb[t] = (1 - a) * self.absorption(t) + a * max(100.0, info["qty"] / secs)
        raw = sum(self.ledger[x].get("vs_mid", 0.0) * self.ledger[x]["qty"] for x in tids if x in self.ledger)
        if raw and info["qty"]:
            # what this unwind cost per share vs the mid when its tenders were accepted (raw edge - made)
            c = float(self.p.get("netting_learn", 0.3))
            self.exec_cost = (1 - c) * self.unwind_cost_ps() + c * (raw - made) / info["qty"]
        self.ex.journal.write("block_done", **rec)
        log.info("BLOCK DONE %s %s %dk in %d s (%s sh/s) | mid %+.2f our way | rested %s crossed %s "
                 "(fills %+.3f vs mid) | made %s vs expected %s", t, info["side"], info["qty"] // 1000, secs,
                 f"{rec['rate']:,}", move, f"{rest:,}", f"{cross:,}", edge, f"{made:+,.0f}", f"{exp:+,.0f}")

    def write_report(self, path: str | None, nlv: float | None, latency: dict) -> None:
        """Machine-readable summary of the heat: the journal's `summary` row (for `ritc practice`), and a separate
        <log>.summary.json only when `path` is given (`run.summary_file = true`)."""
        import json
        doc = {"nlv": nlv, "latency_ms": latency, "profile": self.p.get("profile"),
               "settings": {k: self.p.get(k) for k in ("liquidity_model", "crowd_robust", "auction_skip_adding",
                                                       "min_profit_per_share", "decide_late_ticks")},
               "tenders": {str(k): v for k, v in self.ledger.items()}, "blocks": self.blocks_done,
               "hold_tests": self.hold_test_log,
               "crowd": {"mean": self.crowd.mean, "sd": self.crowd.sd, "n": self.crowd.n, "crowded": self.crowded()},
               "seen": len(self.seen), "taken": len(self.taken)}
        self.ex.journal.write("summary", **json.loads(json.dumps(doc, default=str)))
        if not path:
            return
        try:
            with open(path, "w") as f:
                json.dump(doc, f, indent=1, default=str)
            log.info("heat summary written: %s", path)
        except OSError as exc:
            log.warning("could not write %s: %s", path, exc)

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
                log.info("AUCTION #%s %s %s %dk at %s (%s from the mid when bid): %s", tid,
                         "BUY" if sign > 0 else "SELL", ticker, qty // 1000, e["price"] if e else "?",
                         f"{e.get('vs_mid', 0):.2f}" if e else "?", "WON" if won else "lost")
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
        """HEAT REPORT at the end of the log: every tender (taken or not), every block, the crowd and the totals."""
        if not self.answers and not self.ledger:
            return
        bar = "=" * 100
        log.info(bar)
        log.info("HEAT REPORT   edge = tender price vs the mid in our favour ($/share) | value = what the bot expected "
                 "per share | made = the block's P&L when flat | late = ticks between listing and answer")
        log.info("  %-5s %-4s %-4s %4s %-7s %7s %7s %-8s %9s %9s  %s", "tid", "side", "stk", "size", "kind", "edge",
                 "value", "answer", "expected", "made", "note")
        for tid, a in self.answers.items():
            e = self.ledger.get(tid)
            if e and e["won"] is False:
                ans, exp, made, note = "lost", "-", "-", "auction lost"
            elif e:
                ans = "won" if e["kind"] == "auction" else "TAKEN"
                exp = f"{e['expected']:+,.0f}"
                made = f"{e['realized']:+,.0f}" if e["realized"] is not None else "open"
                note = ""
            elif a["accept"]:          # answered yes but nothing booked: the server refused it
                ans, exp, made = ("failed" if a["kind"] == "fixed" else "rejected"), "-", "-"
                note = "accept failed (expired?)" if a["kind"] == "fixed" else "bid refused at once"
            else:
                ans, exp, made = "declined", "-", "-"
                note = "" if a["reason"].startswith("profit") else a["reason"]
            if a["late"] > 0:
                note = (note + f" answered {a['late']} ticks late").strip()
            log.info("  #%-4s %-4s %-4s %3dk %-7s %7s %7s %-8s %9s %9s  %s", tid, a["side"], a["ticker"],
                     a["qty"] // 1000, a["kind"], f"{a['edge']:+.3f}" if a["edge"] is not None else "-",
                     f"{a['value']:+.3f}" if a["value"] is not None else "-", ans, exp, made, note)
        fixed = [a for a in self.answers.values() if a["kind"] == "fixed"]
        bids = [tid for tid, a in self.answers.items() if a["kind"] != "fixed" and a["accept"]]
        live = [e for e in self.ledger.values() if e["won"] is not False]
        log.info("  fixed tenders: %d seen, %d taken | auctions: %d seen, %d bid, %d won | expected %s, made %s on %d "
                 "closed blocks", len(fixed), sum(a["accept"] for a in fixed), len(self.answers) - len(fixed),
                 len(bids), sum(bool(self.ledger.get(t, {}).get("won")) for t in bids),
                 f"{sum(e['expected'] for e in live):+,.0f}",
                 f"{sum(e['realized'] for e in live if e['realized'] is not None):+,.0f}",
                 sum(e["realized"] is not None for e in live))
        if self.blocks_done:
            log.info("  BLOCKS  %-4s %-4s %4s %5s %8s %8s %8s %9s %9s %9s", "stk", "side", "size", "secs", "rested",
                     "crossed", "mid", "fills", "expected", "made")
            for b in self.blocks_done:
                log.info("          %-4s %-4s %3dk %5d %8s %8s %+8.2f %+9.3f %9s %9s", b["ticker"], b["side"],
                         b["qty"] // 1000, b["secs"], f"{b['rested']:,}", f"{b['crossed']:,}", b["mid_move"],
                         b["fill_edge"], f"{b['expected']:+,}", f"{b['made']:+,}")
            log.info("          (mid = move over the unwind, + = our way; fills = our average fill vs the mid, + = "
                     "earned)")
        log.info("  crowd: impact %+.4f $/10k (sd %.4f, %d tenders scored) -> %s", self.crowd.mean, self.crowd.sd,
                 self.crowd.n, "CROWDED (racing other desks)" if self.crowded() else "no crowd detected")
        log.info(bar)

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
            self.seen_at.setdefault(tid, now)
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
            fixed = bool(t.get("is_fixed_bid", True))
            sg = 1 if str(t.get("action", "BUY")).upper() == "BUY" else -1
            px = t.get("price") if fixed else d.price
            edge = sg * (book.mid - float(px)) if px is not None and book.mid else None   # + = in our favour
            cap = str(t.get("caption") or "")
            self.answers[tid] = {
                "side": str(t.get("action", "")).upper(), "ticker": ticker, "qty": int(t.get("quantity") or 0),
                "kind": "fixed" if fixed else ("WTA" if "Winner_Take" in cap else "reserve" if "Reserve" in cap
                                               else "auction"),
                "edge": edge, "value": d.profit_per_share if d.accept or d.reason.startswith("profit") else None,
                "accept": d.accept, "reason": d.reason, "late": now - self.seen_at.get(tid, now)}
            log.info("TENDER %s %s %s x%s @ %s -> %s (%s)%s  [mid %s%s spread %s vol %.3f/s pos %+d%s]", tid,
                     t.get("action"), ticker, t.get("quantity"), t.get("price"), "ACCEPT" if d.accept else "decline",
                     d.reason,
                     f" ~ ${d.profit_per_share * float(t.get('quantity') or 0):,.0f} expected" if d.accept else "",
                     f"{book.mid:.2f}" if book.mid else "?",
                     f" edge {edge:+.3f}" if edge is not None else "",
                     f"{book.best_ask - book.best_bid:.2f}" if book.best_ask and book.best_bid else "?",
                     self.price_vol(ticker, snap), positions.get(ticker, 0),
                     " crowd ON" if self.crowded() else "")
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
                    self.maybe_hold_test(ticker, sign, int(t["quantity"]), fixed, book, snap, now)
                    self.ledger[tid] = {"ticker": ticker, "side": t.get("action"), "qty": int(t["quantity"]),
                                        "kind": "fixed" if fixed else "auction", "price": d.price,
                                        "expected": d.profit_per_share * int(t["quantity"]), "realized": None,
                                        "won": None if not fixed else True}
                    self.block_tids.setdefault(ticker, []).append(tid)
                    if book.mid and d.price is not None:
                        # how far from the mid the tender price / our bid sat, in our favour
                        self.ledger[tid]["vs_mid"] = round(sign * (book.mid - d.price), 3)
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
                if pos == 0 and t in self.pnl_base and t in snap.securities and (b or t in self.block_info):
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
                    self._block_done(t, snap, made, exp, tids)
                continue
            # New / grown / flipped exposure (e.g. another tender accepted): restart the schedule.
            if (b is None or b.target != target or (pos > 0) != (b.start_pos > 0)
                    or abs(pos) > abs(b.start_pos)):
                kappa = 0.0
                if self.schedule == "almgren_chriss":
                    # sigma: GARCH $/share/tick; eta: from the side we unwind INTO.
                    kappa = ac_kappa(self.price_vol(t, snap), book_eta(snap.book(t), "bid" if pos > 0 else "ask"),
                                     self.ac_lambda)
                self.algo.work(t, pos, target, now, min(now + self.unwind_horizon(t, abs(pos - target)), period_end),
                               kappa=kappa)
                if t not in self.block_info:
                    self.block_info[t] = {"start": now, "mid0": snap.mid(t), "qty": abs(pos - target),
                                          "side": "SELL" if pos > target else "BUY", "fill0": len(self.ex.tca.fills)}
                else:
                    self.block_info[t]["qty"] = max(self.block_info[t]["qty"], abs(pos - target))
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
        if self.holder_drift():
            # Holding is what costs (holder drift): no passive hold, no hold to the bell; cross to the schedule.
            self.algo.passive = set()
            self.algo.passive_from = None
        elif self.hold_budget and snap.ticks_left <= self.hold_until:
            self.algo.passive = set()              # flat by the bell: the schedule crosses what is left
        elif self.hold_budget:
            # STALL-AND-RUN (`stall_and_trend`, solo heats 2026-10-13): every big real loss was a block whose resting
            # side stopped filling WHILE the price ran against it; leave the passive hold only when both hold.
            both = bool(self.cfg.get("execution", {}).get("stall_and_trend", False))

            def stuck(t: str) -> bool:
                return bool(self.stuck_window) and unwind_stuck(self.unwind_hist.get(t, []), now, self.stuck_window,
                                                                self.stuck_horizon)

            def trending(t: str) -> bool:
                return bool(trend_z) and self.trend_against(t, snap.positions.get(t, 0), snap) >= trend_z

            self.algo.passive = {t for t in self.algo.blocks
                                 if hold_within_budget(snap.positions.get(t, 0) - self.algo.blocks[t].target,
                                                       self.price_vol(t, snap), snap.ticks_left, self.hold_budget)
                                 and not ((stuck(t) and trending(t)) if both else (stuck(t) or trending(t)))}
        # SPRINT: cross while the expected further move against the block beats the cost of crossing; rest again
        # once it is under half of that (no flapping on the threshold).
        sprint: set[str] = set()
        if self.p.get("trend_exec", False):
            for t, b in self.algo.blocks.items():
                held = snap.positions.get(t, 0) - b.target
                c = self.cross_threshold(t, held, snap)
                if held and self.predicted_against(t, held, snap) >= (c / 2 if t in self.algo.sprint else c):
                    sprint.add(t)
        first = int(self.cfg.get("execution", {}).get("initial_sprint_ticks", 0))
        gate = self.cfg.get("execution", {}).get("initial_sprint_gate", "always")
        if first and (gate == "always" or (gate == "crowded" and self.crowded())
                      or (gate == "leaning" and self.crowd_learn and self.crowd.n >= 2
                          and (self.crowd.mean or 0.0) > (self.crowd.sd or 0.0))):
            # OBIZHAEVA-WANG start (2026-10-13): cross the first ticks of every block, then the usual unwind. Always:
            # rival markets +$8k..+$23k (t 2.4-5.4), solo markets -$13k..-$23k. `initial_sprint_gate` "crowded" (the
            # learned crowd impact > 2 sd) / "leaning" (> 1 sd): race only when other desks dump the same blocks.
            sprint |= {t for t, b in self.algo.blocks.items() if now - b.start_tick < first}
        # FRONT-LOAD (`front_cross`, 2026-10-15, practice A/B): cross the first `front_cross` share of every new block
        # at once (sprint mode: up to urgent_participation x depth per loop within max_slippage), then rest the rest as
        # usual. Recorded paths + books, 189 real blocks: 25% about neutral on average (-$0.2k..+$2.8k/heat), worst heat
        # -$44k -> -$9k (the losing runs start right after the tender). 0 = off.
        front = float(self.cfg.get("execution", {}).get("front_cross", 0.0))
        front_set: set[str] = set()
        if front > 0:
            for t, b in self.algo.blocks.items():
                size = abs(b.start_pos - b.target)
                # shares SENT to cross since the block started: positions lag in-flight orders (sim rehearsal: "25%"
                # crossed 26-73% judged on positions alone), and an IOC never fills more than it was sent
                sent = self.ex.tca.sent[(t, "aggressive")]
                sent -= self.front_sent0.setdefault((t, b.start_tick), sent)
                done = abs(b.start_pos - snap.positions.get(t, 0))
                if size and max(sent, done) < front * size:
                    front_set.add(t)
            sprint |= front_set
        if self.sprint_walk:
            sprint = self.cap_sprint_walk(sprint, snap)
        for t in sprint - self.algo.sprint:
            if t in front_set:
                log.info("FRONT %s: crossing the first %.0f%% of our %+d block at once", t, 100 * front,
                         snap.positions.get(t, 0))
                continue
            log.info("SPRINT %s: next %s ticks expected %+.3f against our %+d block (run %+.3f, book asym %+.3f), "
                     "crossing costs %.3f: crossing%s", t, self.p.get("trend_window", 10),
                     self.predicted_against(t, snap.positions.get(t, 0), snap), snap.positions.get(t, 0),
                     self.run_against(t, snap.positions.get(t, 0)),
                     self.book_asym(t, snap) if self.book_signal_on() else 0.0,
                     self.cross_threshold(t, snap.positions.get(t, 0), snap), " [ML model]" if self.ml_model else "")
        for t in self.algo.sprint - sprint:
            if t in self.algo.blocks and t not in self.front_seen:
                log.info("SPRINT %s off: resting again", t)
        if front > 0:
            for t in self.front_seen & set(self.algo.blocks) - front_set:
                b = self.algo.blocks[t]
                log.info("FRONT %s done: %+d of the %+d block crossed; resting the rest", t,
                         b.start_pos - snap.positions.get(t, 0), b.start_pos - b.target)
            self.front_seen = front_set
        self.algo.sprint = sprint
        self.algo.passive -= self.algo.sprint
        if self.algo.active and self.fresh_unwind and not self.ex.dry_run:
            def refresh():
                orders = self.client.orders("OPEN")         # orders FIRST, then positions (see step_fresh)
                return orders, self.client.positions()
            self.algo.step_fresh({t: snap.book(t) for t in self.algo.blocks}, snap.positions, now, refresh,
                                 lambda t, pos: self.unwind_target(t, pos, now))
        elif self.algo.active:
            open_ids = None if self.ex.dry_run else {int(o["order_id"]) for o in self.client.orders("OPEN")}
            self.algo.step({t: snap.book(t) for t in self.algo.blocks}, snap.positions, now, open_ids)

    def cap_sprint_walk(self, sprint: set[str], snap: Snapshot) -> set[str]:
        """
        SPRINT WALK CAP (`sprint_max_walk`, practice 2026-10-14): a sprint crosses a few % of the depth every loop at
        the touch + max_slippage, so on a book that refills slowly it walks the price as far as the block lasts:
        CROC 59.5k bought 20.29 -> 21.54 in 8 s (-$24.8k), 54k 20.94 -> 22.39 (-$23.0k), and its own buying fed
        the "run against us" that kept it on. A sprint only pays when it saves more than it costs, and it expects
        to save cents, so it never pays more than `sprint_max_walk` from the mid at the block's first sprint;
        past that it rests.
        """
        keep: set[str] = set()
        live = {(t, b.start_tick) for t, b in self.algo.blocks.items()}
        self.sprint_anchor = {k: v for k, v in self.sprint_anchor.items() if k in live}
        self.algo.sprint_limit = {}
        for t in sprint:
            b = self.algo.blocks[t]
            book = snap.book(t)
            buy = b.target > snap.positions.get(t, 0)
            touch = book.best_ask if buy else book.best_bid
            if touch is None or book.mid is None:
                continue
            key = (t, b.start_tick)
            if key not in self.sprint_anchor:
                self.sprint_anchor[key] = book.mid
            cap = self.sprint_anchor[key] + (self.sprint_walk if buy else -self.sprint_walk)
            if (touch <= cap) if buy else (touch >= cap):
                keep.add(t)
                self.algo.sprint_limit[t] = round(cap, 2)
            elif t in self.algo.sprint:
                log.info("SPRINT %s capped: touch %.2f is past %.2f (start mid %.2f %s %.2f); resting", t, touch, cap,
                         self.sprint_anchor[key], "+" if buy else "-", self.sprint_walk)
        return keep

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
            self.frozen = self.frozen_now(snap)
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
