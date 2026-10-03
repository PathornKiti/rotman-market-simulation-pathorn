"""
The run loop shared by every strategy.

A strategy only implements `step(snapshot)`. The runner handles everything that
is the same across cases and that people get wrong under pressure:

* waits for the case to go ACTIVE (safe to start before the bell)
* polls at a fixed interval and survives transient API errors
* tells the strategy when a new period starts
* calls `wind_down()` in the last N ticks of each period
* ALWAYS cancels resting orders on Ctrl-C or crash
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .book import OrderBook
from .client import RITClient, RITError
from .execution import Executor
from .risk import RiskManager

log = logging.getLogger("ritc.bot")


@dataclass
class Snapshot:
    """Everything a strategy usually needs, fetched once per loop."""
    case: dict
    securities: dict[str, dict]
    _client: RITClient | None = field(default=None, repr=False)
    _books: dict[str, OrderBook] = field(default_factory=dict, repr=False)
    trader_id: str = ""

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
    def positions(self) -> dict[str, int]:
        return {t: int(s.get("position", 0)) for t, s in self.securities.items()}

    def book(self, ticker: str, depth: int = 20) -> OrderBook:
        """Lazily fetched and cached for this loop, our own orders excluded."""
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

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.client = ctx.client
        self.ex = ctx.executor
        self.risk = ctx.risk

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

    def on_stop(self) -> None:
        """Ctrl-C or crash. The runner already cancels every resting order."""


class Runner:
    def __init__(self, strategy: Strategy, interval: float = 0.25, wind_down_ticks: int = 5,
                 max_errors: int = 50):
        self.s = strategy
        self.client = strategy.client
        self.interval = interval
        self.wind_down_ticks = wind_down_ticks
        self.max_errors = max_errors
        self.trader_id = ""

    def snapshot(self) -> Snapshot:
        return Snapshot(case=self.client.case(), securities=self.client.security_map(),
                        _client=self.client, trader_id=self.trader_id)

    def run(self, once: bool = False) -> None:
        log.info("strategy=%s dry_run=%s", self.s.name, self.s.ex.dry_run)
        try:
            self.trader_id = str(self.client.trader().get("trader_id", ""))
        except RITError as exc:
            log.warning("could not read trader id (%s); own orders will not be filtered", exc)

        started, period, errors = False, None, 0
        try:
            while True:
                t0 = time.monotonic()
                try:
                    self.s.ex.sweep()          # kill leftovers of last loop's aggressive orders
                    snap = self.snapshot()
                    status = snap.case.get("status")
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

                    if snap.ticks_left <= self.wind_down_ticks:
                        self.s.wind_down(snap)
                    else:
                        self.s.step(snap)
                    errors = 0
                except RITError as exc:
                    errors += 1
                    log.warning("API error (%d/%d): %s", errors, self.max_errors, exc)
                    if errors >= self.max_errors:
                        raise
                if once:
                    break
                time.sleep(max(0.0, self.interval - (time.monotonic() - t0)))
        except KeyboardInterrupt:
            log.info("Ctrl-C received")
        except Exception:
            log.exception("strategy crashed")
        finally:
            log.info("shutting down: cancelling all resting orders")
            self.s.ex.cancel_all()
            self.s.on_stop()
