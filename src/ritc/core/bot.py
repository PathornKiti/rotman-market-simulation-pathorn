"""
The run loop shared by every strategy.

A strategy only implements `step(snapshot)`. The runner handles everything that
is the same across cases and that people get wrong under pressure:

* waits for the case to go ACTIVE (safe to start before the bell)
* REAL-TIME: a background EventFeed polls news/tenders every ~100 ms and wakes
  the loop immediately when something arrives (no waiting for the next tick).
  The feed is ALSO polled inside every snapshot, in parallel with the prices, so
  a strategy never sees a repriced market without the headline that moved it
* SPEED: case, securities, NLV and every order book the strategy needs are
  fetched in PARALLEL - one round trip per loop instead of one per call
* measures loop latency and warns when the bot is slower than the market
* KILL SWITCH: trips on a configurable drawdown from peak NLV, then cancels,
  flattens and stops adding risk. Before that, a THROTTLE shrinks every limit
  gradually once the drawdown passes `drawdown_soft_start` of the maximum
* tells the strategy when a new period starts; `wind_down()` near the end
* ALWAYS cancels resting orders on Ctrl-C or crash, and prints a TCA report
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .book import OrderBook
from .client import RITClient, RITError
from .execution import Executor
from .feed import EventFeed
from .journal import NULL as NULL_JOURNAL
from .risk import DrawdownGuard, RiskManager

log = logging.getLogger("ritc.bot")


@dataclass
class Snapshot:
    """Everything a strategy usually needs, fetched once per loop."""
    case: dict
    securities: dict[str, dict]
    _client: RITClient | None = field(default=None, repr=False)
    _books: dict[str, OrderBook] = field(default_factory=dict, repr=False)
    trader_id: str = ""
    nlv: float | None = None

    @property
    def tick(self) -> int:
        return int(self.case.get("tick", 0))

    @property
    def period(self) -> int:
        return int(self.case.get("period", 1))

    @property
    def ticks_per_period(self) -> int:
        return int(self.case.get("ticks_per_period", 300))

    @property
    def ticks_left(self) -> int:
        return self.ticks_per_period - self.tick

    @property
    def abs_tick(self) -> int:
        return (self.period - 1) * self.ticks_per_period + self.tick

    @property
    def positions(self) -> dict[str, int]:
        return {t: int(s.get("position", 0)) for t, s in self.securities.items()}

    def book(self, ticker: str, depth: int = 20) -> OrderBook:
        """Prefetched in parallel by the Runner; fetched lazily if not. Own orders excluded."""
        if ticker not in self._books:
            payload = self._client.book(ticker, depth) if self._client else {}
            self._books[ticker] = OrderBook.from_api(payload, ticker, self.trader_id or None)
        return self._books[ticker]

    def quote(self, ticker: str) -> tuple[float | None, float | None]:
        s = self.securities.get(ticker, {})
        bid, ask = s.get("bid"), s.get("ask")
        return (float(bid) if bid else None), (float(ask) if ask else None)

    def mid(self, ticker: str) -> float | None:
        bid, ask = self.quote(ticker)
        if bid and ask:
            return (bid + ask) / 2
        last = self.securities.get(ticker, {}).get("last")
        return float(last) if last else (bid or ask)


@dataclass
class Context:
    client: RITClient
    executor: Executor
    risk: RiskManager
    cfg: dict[str, Any]


class Strategy(ABC):
    """Subclass this. Keep decision logic in pure functions so it can be unit-tested."""

    name = "base"
    wants_news = False         # set True to get news via the real-time feed
    wants_tenders = False      # set True to get tenders via the real-time feed

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.client = ctx.client
        self.ex = ctx.executor
        self.risk = ctx.risk
        self.feed: EventFeed | None = None       # attached by the Runner
        self._last_news_id = 0

    # ----------------------------------------------------------- data helpers
    @property
    def book_tickers(self) -> list[str]:
        """Tickers whose books the Runner prefetches in parallel each loop."""
        return []

    def new_news(self) -> list[dict]:
        """New news items, oldest first - from the real-time feed when running."""
        if self.feed is not None and self.feed.running:
            return self.feed.drain_news()
        items = sorted(self.client.news(since=self._last_news_id or None), key=lambda n: n.get("news_id", 0))
        items = [n for n in items if int(n.get("news_id", 0)) > self._last_news_id]
        if items:
            self._last_news_id = int(items[-1]["news_id"])
        return items

    def current_tenders(self) -> list[dict]:
        if self.feed is not None and self.feed.running:
            return self.feed.tenders()
        return self.client.tenders()

    # ----------------------------------------------------------- risk sizing
    def sized(self, n: float) -> int:
        """
        Scale a RISK-ADDING size by the drawdown throttle (1.0 = full size). Use it on
        entries only: exits and hedges always run at full size, so a throttled bot
        still gets out of what it holds.
        """
        return int(n * self.risk.throttle)

    def edge_mult(self) -> float:
        """Demand proportionally more edge while throttled (1 / throttle, capped at 4x)."""
        return 1.0 / max(self.risk.throttle, 0.25)

    # ------------------------------------------------------------------ hooks
    def on_start(self, snap: Snapshot) -> None:
        """Called once when the case first goes ACTIVE."""

    def on_new_period(self, snap: Snapshot) -> None:
        """Called at the start of every period after the first."""

    @abstractmethod
    def step(self, snap: Snapshot) -> None:
        """One decision cycle."""

    def wind_down(self, snap: Snapshot) -> None:
        """Last ticks of a period. Default: cancel quotes, stop adding risk."""
        self.ex.cancel_all()

    def flatten(self, snap: Snapshot, slippage: float = 0.10) -> None:
        """Emergency exit: cancel everything and cross out of every position."""
        self.ex.cancel_all()
        for t, pos in snap.positions.items():
            if not pos or not snap.securities.get(t, {}).get("is_tradeable", True):
                continue
            bid, ask = snap.quote(t)
            touch = bid if pos > 0 else ask
            if touch:
                self.ex.limit(t, "SELL" if pos > 0 else "BUY", abs(pos),
                              touch - slippage if pos > 0 else touch + slippage)

    def on_stop(self) -> None:
        """Ctrl-C or crash. The runner already cancels every resting order."""


class LatencyStats:
    def __init__(self, n: int = 500):
        self.buf: deque[float] = deque(maxlen=n)

    def add(self, ms: float) -> None:
        self.buf.append(ms)

    def pct(self, q: float) -> float:
        if not self.buf:
            return 0.0
        s = sorted(self.buf)
        return s[min(len(s) - 1, int(q * len(s)))]

    def __str__(self) -> str:
        return f"loop p50 {self.pct(0.5):.0f} ms, p95 {self.pct(0.95):.0f} ms, max {self.pct(1.0):.0f} ms"


class Runner:
    def __init__(self, strategy: Strategy, interval: float = 0.25, wind_down_ticks: int = 5,
                 max_errors: int = 50, max_drawdown: float = 0.0, feed_poll: float = 0.1,
                 drawdown_soft_start: float = 0.5, drawdown_floor: float = 0.25,
                 slow_loop_ms: float = 500.0, use_feed: bool = True):
        self.s = strategy
        self.client = strategy.client
        self.interval = interval
        self.wind_down_ticks = wind_down_ticks
        self.max_errors = max_errors
        self.guard = DrawdownGuard(max_drawdown, drawdown_soft_start, drawdown_floor)
        self.feed_poll = feed_poll
        self.slow_loop_ms = slow_loop_ms
        self.use_feed = use_feed
        self.trader_id = ""
        self.feed: EventFeed | None = None
        self.on_loop: Callable[[], None] | None = None   # test/tuner hook, e.g. advance a lock-step simulator
        self.latency = LatencyStats()
        self.loops = 0
        self.journal = NULL_JOURNAL          # run journal for the post-trade report (`ritc run` opens one)
        self._tick_ms = 0.0

    def snapshot(self) -> Snapshot:
        """Case + securities + trader + all prefetch books, in parallel."""
        books = list(dict.fromkeys(self.s.book_tickers))
        calls = [self.client.case, self.client.security_map, self.client.trader]
        calls += [lambda t=t: self.client.book(t, 20) for t in books]
        if self.feed is not None and self.feed.running:
            # News must be at least as fresh as the prices: otherwise the bot can trade a
            # repriced market against a stale forecast in the gap before the feed thread polls.
            calls.append(self.feed.poll_once)
        res = self.client.parallel(calls)
        for r in res[:2]:
            if isinstance(r, Exception):
                raise r if isinstance(r, RITError) else RITError(str(r))
        case, secs, trader = res[0], res[1], res[2]
        snap = Snapshot(case=case, securities=secs, _client=self.client, trader_id=self.trader_id,
                        nlv=float(trader.get("nlv")) if isinstance(trader, dict) and trader.get("nlv") is not None
                        else None)
        for t, payload in zip(books, res[3:3 + len(books)]):
            if isinstance(payload, dict):
                snap._books[t] = OrderBook.from_api(payload, t, self.trader_id or None)
        return snap

    def run(self, once: bool = False) -> None:
        log.info("strategy=%s dry_run=%s", self.s.name, self.s.ex.dry_run)
        try:
            trader = self.client.trader()
            self.trader_id = str(trader.get("trader_id", ""))
            try:
                case = self.client.case()
            except RITError:
                case = {}
            self.journal.write("start", strategy=self.s.name, dry_run=self.s.ex.dry_run, trader=self.trader_id,
                               case=case.get("name"), ticks_per_period=case.get("ticks_per_period"),
                               periods=case.get("total_periods"),
                               cfg={k: self.s.cfg.get(k) for k in ("case", "strategy", "execution", "risk")})
        except RITError as exc:
            log.warning("could not read trader id (%s); own orders will not be filtered", exc)

        feed = None
        if self.use_feed and (self.s.wants_news or self.s.wants_tenders):
            feed = EventFeed(self.client, self.feed_poll, news=self.s.wants_news,
                             tenders=self.s.wants_tenders).start()
            self.s.feed = feed
            self.feed = feed
            log.info("real-time feed on (news=%s tenders=%s, every %.0f ms)",
                     self.s.wants_news, self.s.wants_tenders, 1000 * self.feed_poll)

        started, period, errors, halted = False, None, 0, False
        try:
            while True:
                t0 = time.monotonic()
                try:
                    self.s.ex.sweep()          # kill leftovers of last loop's aggressive orders
                    snap = self.snapshot()
                    self.s.ex.arrival = snap.mid
                    status = snap.case.get("status")
                    if snap.tick != self.journal.tick and status == "ACTIVE":
                        self.journal.tick = snap.tick
                        self.journal.write(
                            "tick", period=snap.period, nlv=snap.nlv, pos=snap.positions,
                            bid={t: v.get("bid") for t, v in snap.securities.items()},
                            ask={t: v.get("ask") for t, v in snap.securities.items()},
                            last={t: v.get("last") for t, v in snap.securities.items()},
                            loop_ms=round(self._tick_ms, 1))           # slowest loop since the last tick
                        self._tick_ms = 0.0
                    if status != "ACTIVE":
                        if status == "STOPPED" and started:
                            log.info("case stopped - exiting")
                            break
                        time.sleep(0.5)
                        continue
                    if not started:
                        started, period = True, snap.period
                        self.s.on_start(snap)
                    elif snap.period != period:
                        period = snap.period
                        log.info("--- period %d ---", period)
                        self.s.on_new_period(snap)

                    if self.guard.update(snap.nlv):
                        halted = True
                        self.s.risk.halted = True
                        log.error("KILL SWITCH: NLV %.2f is %.2f below peak %.2f - flattening and halting",
                                  snap.nlv, self.guard.peak - snap.nlv, self.guard.peak)
                    thr = self.guard.throttle()
                    prev = self.s.risk.throttle
                    if not halted and (abs(thr - prev) >= 0.1 or (thr < 1.0) != (prev < 1.0)):
                        log.warning("RISK THROTTLE %.0f%% of limits (drawdown %.0f of max %.0f)",
                                    100 * thr, self.guard.drawdown, self.guard.max_drawdown)
                    self.s.risk.throttle = thr
                    if halted:
                        self.s.flatten(snap)
                    elif snap.ticks_left <= self.wind_down_ticks:
                        self.s.wind_down(snap)
                    else:
                        self.s.step(snap)
                    errors = 0
                except RITError as exc:
                    errors += 1
                    log.warning("API error (%d/%d): %s", errors, self.max_errors, exc)
                    self.journal.write("error", where="loop", n=errors, msg=str(exc)[:300])
                    if errors >= self.max_errors:
                        raise

                ms = 1000 * (time.monotonic() - t0)
                self.latency.add(ms)
                self._tick_ms = max(self._tick_ms, ms)
                self.loops += 1
                if ms > self.slow_loop_ms:
                    log.warning("slow loop: %.0f ms (market may have moved under you)", ms)
                    self.journal.write("slow_loop", ms=round(ms))
                if self.loops % 200 == 0:
                    log.info("%s%s", self.latency, f", feed {feed.latency_ms:.0f} ms" if feed else "")
                if self.loops % 20 == 0:
                    self.s.ex.reconcile()
                if self.on_loop is not None:
                    self.on_loop()
                if once:
                    break
                remaining = max(0.0, self.interval - (time.monotonic() - t0))
                if feed is not None:
                    feed.wait(remaining)       # wakes early on news / new tenders
                else:
                    time.sleep(remaining)
        except KeyboardInterrupt:
            log.info("Ctrl-C received")
        except Exception:
            log.exception("strategy crashed")
        finally:
            log.info("shutting down: cancelling all resting orders")
            if feed is not None:
                feed.stop()
            self.s.ex.cancel_all()
            self.s.on_stop()
            self.s.ex.reconcile()
            if self.loops:
                log.info("%s over %d loops", self.latency, self.loops)
            if not self.s.ex.dry_run:
                log.info("\n%s", self.s.ex.tca.report())
            try:
                nlv = self.client.trader().get("nlv")
            except RITError:
                nlv = None
            self.journal.write("end", nlv=nlv, loops=self.loops, p50=self.latency.pct(0.5),
                               p95=self.latency.pct(0.95), max=self.latency.pct(1.0),
                               tca=[{"ticker": t, "style": st_, "qty": a.qty, "cost": a.cost}
                                    for (t, st_), a in self.s.ex.tca.summary().items()])
            self.journal.close()
