"""
DERIVATIVES TRADING - volatility arbitrage on options, delta-hedged.

The edge
--------
In the RITC volatility case the news tells you the realised volatility of the
underlying (this week) and a range for next week. Option prices in the market
imply a volatility that lags that information. When

    forecast vol  >  implied vol   ->  options are cheap: BUY them (long vega)
    forecast vol  <  implied vol   ->  options are rich:  SELL them (short vega)

and we neutralise the direction risk by trading the underlying to keep the
portfolio delta inside the case's delta limit (breaching it is fined).

What we trade
-------------
* Every option whose |forecast - IV| exceeds `vol_edge`, sized proportional to
  the edge up to `max_contracts`. Near-the-money options carry the most vega
  per dollar of commission, so we weight by vega.
* Put-call parity violations (risk-free), if `parity_arb = true`.
* The underlying, only for delta hedging.

Volatility through time
-----------------------
The forecast for each option blends two views (`blend_forecast`):

* the NEWS forecast (exact realised vol this week, a range for next week), and
* a GARCH(1,1) fitted on the underlying's tick returns, projected over the
  option's REMAINING LIFE - so a 10-tick option and a 400-tick option get
  different forecasts when current vol is far from its long-run level.

    forecast = (1 - garch_weight) * news + garch_weight * garch_term_vol(ticks to expiry)

Default garch_weight is 0: when the case TELLS you this week's realised vol, a
model of the past only dilutes it (measured: blending 30% GARCH lost money on
5 of 6 simulator seeds). GARCH is the forecast before the first vol headline and
for any case with no vol news - set garch_weight > 0 only if practice shows the
news is noisy.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from ..core.bot import Snapshot, Strategy
from ..pricing.news import parse_vol_news
from ..pricing.options import bs_greeks, implied_vol, ticks_to_years
from ..pricing.timeseries import OnlineGarch

log = logging.getLogger("ritc.derivatives")


@dataclass(frozen=True)
class OptionSpec:
    ticker: str
    is_call: bool
    strike: float
    expiry_tick: int        # absolute tick (across periods) at which it expires


def parse_option(ticker: str, pattern: str, expiries: dict[str, int], default_expiry: int) -> OptionSpec | None:
    m = re.match(pattern, ticker)
    if not m:
        return None
    g = m.groupdict()
    exp_code = g.get("exp") or ""
    return OptionSpec(ticker, g["cp"].upper() == "C", float(g["strike"]),
                      int(expiries.get(exp_code, default_expiry)))


@dataclass(frozen=True)
class OptionSignal:
    ticker: str
    iv: float
    theo: float
    delta: float
    vega: float
    target: int             # desired signed contracts


def option_signal(spec: OptionSpec, S: float, bid: float | None, ask: float | None, T: float, r: float,
                  forecast: float, vol_edge: float, full_edge: float, max_contracts: int,
                  fee_per_unit: float, position: int = 0, exit_edge: float = 0.0) -> OptionSignal | None:
    """
    Target position for one option. Pure function.
    Hysteresis: enter at |edge| >= vol_edge, but keep an existing position until the
    edge has converged to `exit_edge` - otherwise IV noise around the entry threshold
    makes us pay the spread in and out over and over.
    """
    if not bid or not ask or bid <= 0 or ask <= bid or T <= 0 or S <= 0:
        return None
    mid = (bid + ask) / 2
    iv = implied_vol(mid, S, spec.strike, T, r, spec.is_call)
    if iv is None:
        return None
    g = bs_greeks(S, spec.strike, T, r, forecast, spec.is_call)
    edge = forecast - iv
    target = 0
    if abs(edge) >= vol_edge:
        # Only trade if the executable price (not the mid) still clears costs.
        if edge > 0 and g.price - ask > fee_per_unit:
            target = 1
        elif edge < 0 and bid - g.price > fee_per_unit:
            target = -1
        scale = min(1.0, abs(edge) / max(full_edge, 1e-9))
        target *= int(round(max_contracts * scale))
    if target == 0 and position != 0 and edge * (1 if position > 0 else -1) > exit_edge:
        target = position          # edge still on our side: hold, don't churn
    return OptionSignal(spec.ticker, iv, g.price, g.delta, g.vega, target)


def portfolio_delta(positions: dict[str, int], deltas: dict[str, float], underlying: str,
                    multiplier: int) -> float:
    d = float(positions.get(underlying, 0))
    for t, dl in deltas.items():
        d += positions.get(t, 0) * multiplier * dl
    return d


def hedge_order(delta: float, limit: float, band: float, max_order: int) -> tuple[str, int] | None:
    """Re-hedge to zero once |delta| exceeds band*limit. Returns (action, shares)."""
    if abs(delta) <= band * limit:
        return None
    qty = min(int(abs(delta)), max_order)
    return ("SELL" if delta > 0 else "BUY"), qty


def parity_trades(call_bid, call_ask, put_bid, put_ask, s_bid, s_ask, K, T, r, cost) -> list[tuple[str, str]] | None:
    """
    Executable put-call parity arbitrage per unit (1 option vs 1 share).
    Conversion: sell call, buy put, buy stock when  C_bid - P_ask - S_ask + K e^-rT > cost.
    Reversal:   buy call, sell put, sell stock when P_bid - C_ask + S_bid - K e^-rT > cost.
    """
    pv_k = K * math.exp(-r * T)
    if None in (call_bid, call_ask, put_bid, put_ask, s_bid, s_ask) or min(call_bid, put_bid, s_bid) <= 0:
        return None
    if call_bid - put_ask - s_ask + pv_k > cost:
        return [("call", "SELL"), ("put", "BUY"), ("stock", "BUY")]
    if put_bid - call_ask + s_bid - pv_k > cost:
        return [("call", "BUY"), ("put", "SELL"), ("stock", "SELL")]
    return None


def blend_forecast(news_vol: float | None, garch_vol: float | None, garch_weight: float,
                   fallback: float) -> float:
    """Annualised vol forecast from news and/or GARCH. Pure function."""
    if news_vol is not None and garch_vol is not None:
        return (1 - garch_weight) * news_vol + garch_weight * garch_vol
    if news_vol is not None:
        return news_vol
    if garch_vol is not None:
        return garch_vol
    return fallback


class DerivativesStrategy(Strategy):
    name = "derivatives"
    wants_news = True              # vol / delta-limit headlines via the real-time feed

    def __init__(self, ctx):
        super().__init__(ctx)
        c, s = self.cfg.get("case", {}), self.cfg.get("strategy", {})
        self.und: str = c.get("underlying", "RTM")
        self.pattern: str = c.get("option_regex", r"^(?P<und>[A-Z]+)(?P<exp>\d*)(?P<cp>[CP])(?P<strike>\d+(?:\.\d+)?)$")
        self.expiries: dict[str, int] = {str(k): int(v) for k, v in c.get("expiry_ticks", {}).items()}
        self.default_expiry = int(c.get("default_expiry_tick", 600))
        self.ticks_per_year = float(c.get("ticks_per_year", 3600))
        self.r = float(c.get("rate", 0.0))
        self.mult = int(c.get("multiplier", 100))
        self.opt_fee = float(c.get("option_fee", 1.0))        # per contract
        self.p = s
        self.forecast: float = float(s.get("initial_vol", 0.20))
        self.delta_limit: float = float(s.get("delta_limit", 5000))
        self.news_seen = False
        self.specs: dict[str, OptionSpec] = {}
        self.garch = OnlineGarch(window=int(s.get("garch_window", 600)), min_obs=int(s.get("garch_min_obs", 60)))
        self.last_tick = -1

    def on_start(self, snap: Snapshot) -> None:
        for t in snap.securities:
            if t == self.und:
                continue
            spec = parse_option(t, self.pattern, self.expiries, self.default_expiry)
            if spec:
                self.specs[t] = spec
        log.info("found %d options on %s", len(self.specs), self.und)
        # Warm GARCH up from the price history the case already has (newest first).
        try:
            closes = [h.get("close") for h in reversed(self.client.history(self.und)) if h.get("close")]
            for c in closes:
                self.garch.add_price(float(c))
            if closes:
                log.info("GARCH warm-up on %d historical prices", len(closes))
        except Exception as exc:
            log.debug("no history for warm-up: %s", exc)

    def vol_forecast(self, ticks_to_expiry: int) -> float:
        g = None
        if self.garch.ready and (self.p.get("garch_weight", 0.0) > 0 or not self.news_seen):
            g = self.garch.annualised(ticks_to_expiry, self.ticks_per_year)
        return blend_forecast(self.forecast if self.news_seen else None, g,
                              self.p.get("garch_weight", 0.0), self.forecast)

    def read_news(self) -> None:
        for item in self.new_news():
            v = parse_vol_news(f"{item.get('headline', '')} {item.get('body', '')}")
            if v.delta_limit:
                self.delta_limit = float(v.delta_limit)
                log.info("NEWS delta limit -> %d", v.delta_limit)
            # Realised vol for the current week is the best forecast for the next few
            # ticks; a forecast range applies to next week. Blend toward the range mid.
            if v.realized is not None:
                self.news_seen = True
                self.forecast = v.realized
                log.info("NEWS realised vol -> %.1f%%", 100 * v.realized)
            elif v.forecast_mid is not None:
                self.news_seen = True
                w = self.p.get("range_weight", 0.5)
                self.forecast = (1 - w) * self.forecast + w * v.forecast_mid
                log.info("NEWS vol range %.0f-%.0f%% -> forecast %.1f%%",
                         100 * v.forecast_lo, 100 * v.forecast_hi, 100 * self.forecast)

    def step(self, snap: Snapshot) -> None:
        self.read_news()
        S = snap.mid(self.und)
        if not S:
            return
        if snap.tick != self.last_tick:          # one GARCH observation per tick
            self.last_tick = snap.tick
            self.garch.add_price(S)
        abs_tick = (snap.period - 1) * snap.ticks_per_period + snap.tick
        positions = snap.positions
        deltas: dict[str, float] = {}
        fee_per_unit = self.opt_fee / self.mult + self.p.get("min_price_edge", 0.02)
        max_c = int(self.p.get("max_contracts", 100))

        min_ticks = int(self.p.get("min_ticks_to_expiry", 10))
        for t, spec in self.specs.items():
            if spec.expiry_tick - abs_tick < min_ticks:
                continue          # expiring: no vega left, only pin risk
            T = ticks_to_years(spec.expiry_tick - abs_tick, self.ticks_per_year)
            bid, ask = snap.quote(t)
            fcst = self.vol_forecast(spec.expiry_tick - abs_tick)
            sig = option_signal(spec, S, bid, ask, T, self.r, fcst,
                                self.p.get("vol_edge", 0.02), self.p.get("full_edge", 0.06),
                                max_c, fee_per_unit, positions.get(t, 0), self.p.get("exit_edge", 0.0))
            if sig is None:
                continue
            deltas[t] = sig.delta
            pos = positions.get(t, 0)
            diff = sig.target - pos
            # Hysteresis: don't churn for a handful of contracts.
            if abs(diff) < self.p.get("min_trade_contracts", 5):
                continue
            action = "BUY" if diff > 0 else "SELL"
            qty = min(abs(diff), self.risk.room(t, action, positions))
            if qty <= 0:
                continue
            # Protective limit: never pay more than theo - costs (buy) / less than theo + costs (sell).
            px = sig.theo - fee_per_unit if action == "BUY" else sig.theo + fee_per_unit
            log.info("%s IV %.1f%% vs fcst %.1f%% -> target %+d (pos %+d)", t, 100 * sig.iv,
                     100 * fcst, sig.target, pos)
            self.ex.limit(t, action, qty, px)
            positions[t] = pos + (qty if action == "BUY" else -qty)

        if self.p.get("parity_arb", True):
            self.parity(snap, abs_tick, positions)

        delta = portfolio_delta(positions, deltas, self.und, self.mult)
        if not any(positions.get(t, 0) for t in self.specs) and positions.get(self.und, 0):
            delta = float(positions[self.und])     # no options left: the stock is pure risk, flatten it
            h = (("SELL" if delta > 0 else "BUY"), min(abs(int(delta)), self.ex.max_size(self.und)))
        else:
            h = hedge_order(delta, self.delta_limit, self.p.get("hedge_band", 0.15), self.ex.max_size(self.und))
        if h:
            bid, ask = snap.quote(self.und)
            px = (ask or S) + 0.10 if h[0] == "BUY" else (bid or S) - 0.10
            log.info("HEDGE delta %+.0f (limit %.0f) -> %s %d %s", delta, self.delta_limit, h[0], h[1], self.und)
            self.ex.limit(self.und, h[0], h[1], px)

    def parity(self, snap: Snapshot, abs_tick: int, positions: dict[str, int]) -> None:
        by_key: dict[tuple[float, int], dict[str, str]] = {}
        for t, sp in self.specs.items():
            if sp.expiry_tick - abs_tick < int(self.p.get("min_ticks_to_expiry", 10)):
                continue
            by_key.setdefault((sp.strike, sp.expiry_tick), {})["call" if sp.is_call else "put"] = t
        s_bid, s_ask = snap.quote(self.und)
        size = int(self.p.get("parity_contracts", 10))
        for (K, exp), legs in by_key.items():
            if "call" not in legs or "put" not in legs:
                continue
            cb, ca = snap.quote(legs["call"])
            pb, pa = snap.quote(legs["put"])
            T = ticks_to_years(exp - abs_tick, self.ticks_per_year)
            cost = 2 * self.opt_fee / self.mult + 0.02 + self.p.get("parity_edge", 0.05)
            trades = parity_trades(cb, ca, pb, pa, s_bid, s_ask, K, T, self.r, cost)
            if not trades:
                continue
            log.info("PARITY K=%.1f exp=%d %s", K, exp, trades)
            for leg, action in trades:
                if leg == "stock":
                    px = s_ask if action == "BUY" else s_bid
                    self.ex.limit(self.und, action, size * self.mult, px)
                else:
                    t = legs[leg]
                    b, a = (cb, ca) if leg == "call" else (pb, pa)
                    self.ex.limit(t, action, size, a if action == "BUY" else b)
