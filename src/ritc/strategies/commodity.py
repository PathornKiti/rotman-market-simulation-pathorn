"""
COMMODITY TRADING - cost-of-carry arbitrage + inventory-news momentum.

Two independent engines, both on a spot commodity and its futures:

1. Carry arbitrage (low risk)
   A future should trade at  F = S + carry_per_tick * ticks_to_expiry
   (storage + financing). When the executable basis strays from that by more
   than costs + `carry_edge`:
       F too rich -> SELL future, BUY spot  (cash-and-carry)
       F too cheap -> BUY future, SELL spot (reverse; needs shortable spot)
   The trade is closed once the basis converges inside `carry_exit`, or held to
   expiry, where the future settles to spot and the basis is exactly zero.

   Bookkeeping: the carry book is tracked from FILLS, not orders, and the spot
   leg is re-hedged every loop to exactly offset the carry futures. A one-legged
   fill, or a future that expired and cash-settled, would otherwise leave a naked
   spot position (the expired-future case left the whole spot hedge open).
   Each pair is sized against every limit for the PACKAGE (future + spot), since
   both legs use the same gross limit.

2. News momentum (directional, sized small)
   Inventory reports move price: a bigger-than-expected BUILD is bearish, a
   bigger DRAW is bullish. RIT prices adjust over several ticks, so we trade
   the front future immediately in the surprise direction and exit after
   `news_hold_ticks` (or earlier if price already moved `news_target`, or
   stopped out once it moved `news_stop` x the expected move the wrong way).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.bot import Snapshot, Strategy
from ..pricing.news import parse_inventory_news

log = logging.getLogger("ritc.commodity")


def fair_basis(carry_per_tick: float, ticks_to_expiry: int) -> float:
    return carry_per_tick * max(ticks_to_expiry, 0)


@dataclass(frozen=True)
class CarrySignal:
    direction: int           # +1 = long near/short far (far rich); -1 = reverse; 0 = none
    mispricing: float        # executable mispricing per unit beyond fair basis and costs


def carry_signal(near_bid: float | None, near_ask: float | None, far_bid: float | None, far_ask: float | None,
                 basis_fair: float, cost: float, edge: float, near_shortable: bool = True) -> CarrySignal:
    """
    'near' is the spot (or the nearer future), 'far' the future.
    Far rich:  far_bid - near_ask - basis_fair - cost > edge  -> buy near, sell far.
    Far cheap: near_bid - far_ask + basis_fair - cost > edge  -> sell near, buy far.
    """
    if None in (near_bid, near_ask, far_bid, far_ask):
        return CarrySignal(0, 0.0)
    rich = far_bid - near_ask - basis_fair - cost
    if rich > edge:
        return CarrySignal(+1, rich)
    cheap = near_bid - far_ask + basis_fair - cost
    if cheap > edge and near_shortable:
        return CarrySignal(-1, cheap)
    return CarrySignal(0, max(rich, cheap))


def news_trade(surprise: float, impact_per_unit: float, threshold: float, max_size: int,
               full_move: float) -> tuple[int, float]:
    """
    Expected price move = -impact * surprise (build is bearish).
    Returns (signed size, expected move). Size scales with the move up to max_size.
    """
    move = -impact_per_unit * surprise
    if abs(move) < threshold:
        return 0, move
    size = int(max_size * min(1.0, abs(move) / max(full_move, 1e-9)))
    return (size if move > 0 else -size), move


@dataclass
class BayesImpact:
    """
    Online Bayesian estimate of the price impact per unit of inventory surprise.

    Model: move = beta * x + noise,  x = -surprise (a build is bearish),
           noise ~ N(0, noise_sd^2),  prior beta ~ N(prior_mean, prior_sd^2).
    The normal-normal posterior is closed-form (conjugate), so each report updates
    it exactly:  precision += x^2 / noise^2,  beta = (prior terms + x * move / noise^2) / precision.
    A mis-calibrated `impact_per_unit` is corrected by the market's own reactions
    within a heat, and the posterior sd says how much to trust it.
    """
    mean: float
    sd: float
    noise_sd: float = 0.10
    n: int = 0

    def update(self, x: float, move: float) -> None:
        if x == 0:
            return
        prec0, prec_obs = 1 / self.sd ** 2, x * x / self.noise_sd ** 2
        prec = prec0 + prec_obs
        self.mean = (self.mean * prec0 + (move / x) * prec_obs) / prec
        self.sd = prec ** -0.5
        self.n += 1


@dataclass
class NewsPosition:
    ticker: str
    qty: int
    entry_tick: int
    entry_px: float
    target_move: float


class CommodityStrategy(Strategy):
    name = "commodity"
    wants_news = True              # inventory reports via the real-time feed: first in wins

    def __init__(self, ctx):
        super().__init__(ctx)
        c, s = self.cfg.get("case", {}), self.cfg.get("strategy", {})
        self.spot: str = c.get("spot", "CL")
        self.futures: dict[str, int] = {k: int(v) for k, v in c.get("futures", {}).items()}
        self.carry = float(c.get("carry_per_tick", 0.0))
        self.fees: dict[str, float] = {k: float(v) for k, v in c.get("fee", {}).items()}
        self.ratio: dict[str, float] = {k: float(v) for k, v in c.get("hedge_ratio", {}).items()}
        self.spot_shortable = bool(c.get("spot_shortable", False))
        self.p = s
        self.news_pos: list[NewsPosition] = []
        # Futures held by the carry engine only (news trades are tracked separately),
        # so a converged-basis exit never closes a news position by mistake.
        self.carry_pos: dict[str, int] = {}
        # News impact: fixed from config, or learned online (impact_mode = "bayes").
        self.impact = BayesImpact(float(s.get("impact_per_unit", 0.25)), float(s.get("impact_prior_sd", 0.15)),
                                  float(s.get("impact_noise_sd", 0.10)))
        self.pending_obs: list[tuple[int, float, float]] = []      # (tick, spot mid at news, x)

    # ----------------------------------------------------------------- helpers
    def abs_tick(self, snap: Snapshot) -> int:
        return (snap.period - 1) * snap.ticks_per_period + snap.tick

    def front(self, snap: Snapshot) -> str | None:
        now = self.abs_tick(snap)
        live = [(exp, t) for t, exp in self.futures.items() if exp > now + 5 and t in snap.securities]
        return min(live)[1] if live else None

    # -------------------------------------------------------------------- step
    def step(self, snap: Snapshot) -> None:
        self.expire(snap)
        self.run_news(snap)
        positions = snap.positions         # shared, so the re-hedge sees this loop's carry fills
        self.run_carry(snap, positions)
        self.rehedge_spot(snap, positions)

    def expire(self, snap: Snapshot) -> None:
        """Forget carry/news positions in futures that have expired (cash-settled to spot)."""
        now = self.abs_tick(snap)
        for fut in list(self.carry_pos):
            sec = snap.securities.get(fut)
            if self.futures.get(fut, 0) <= now or sec is None or not sec.get("is_tradeable", True):
                if self.carry_pos.pop(fut):
                    log.info("CARRY %s expired - spot hedge will be unwound", fut)
        self.news_pos = [n for n in self.news_pos
                         if self.futures.get(n.ticker, 0) > now
                         and snap.securities.get(n.ticker, {}).get("is_tradeable", True)]

    def spot_target(self) -> int:
        return -int(round(sum(q * self.ratio.get(f, 1.0) for f, q in self.carry_pos.items())))

    def rehedge_spot(self, snap: Snapshot, positions: dict[str, int]) -> None:
        """Keep the spot leg exactly offsetting the carry futures we actually hold."""
        if self.ex.dry_run:
            return                         # nothing fills in a dry run: positions never move
        diff = self.spot_target() - positions.get(self.spot, 0)
        if abs(diff) < int(self.p.get("spot_tolerance", 1)):
            return
        bid, ask = snap.quote(self.spot)
        if bid is None or ask is None:
            return
        action = "BUY" if diff > 0 else "SELL"
        slip = self.p.get("carry_slippage", 0.02)
        log.info("SPOT REHEDGE %s %d %s (target %+d)", action, abs(diff), self.spot, self.spot_target())
        self.ex.limit(self.spot, action, abs(diff), ask + slip if action == "BUY" else bid - slip)

    def run_carry(self, snap: Snapshot, positions: dict[str, int]) -> None:
        now = self.abs_tick(snap)
        clip = int(self.p.get("carry_clip", 20))
        for fut, exp in self.futures.items():
            if fut not in snap.securities or exp <= now + self.p.get("min_ticks_to_expiry", 10):
                continue
            ratio = self.ratio.get(fut, 1.0)           # spot units per future contract
            nb, na = snap.quote(self.spot)
            fb, fa = snap.quote(fut)
            basis = fair_basis(self.carry, exp - now)
            cost = self.fees.get(fut, 0.0) / ratio + self.fees.get(self.spot, 0.0)
            sig = carry_signal(nb, na, fb, fa, basis, cost, self.p.get("carry_edge", 0.15), self.spot_shortable)
            fpos = self.carry_pos.get(fut, 0)

            # Exit: basis converged and we hold the trade.
            mid_mis = (snap.mid(fut) or 0) - (snap.mid(self.spot) or 0) - basis
            if fpos and abs(mid_mis) < self.p.get("carry_exit", 0.03):
                q = min(abs(fpos), clip)
                f_act = "BUY" if fpos < 0 else "SELL"
                s_act = "SELL" if f_act == "BUY" else "BUY"
                log.info("CARRY converged on %s (%.3f) -> close %d", fut, mid_mis, q)
                got = self._pair(fut, f_act, q, s_act, int(q * ratio), snap, positions)
                self.carry_pos[fut] = fpos + (got if f_act == "BUY" else -got)
                continue

            if sig.direction == 0:
                continue
            # Don't pyramid past the configured carry size on one future.
            if abs(fpos) >= int(self.p.get("carry_max", 100)):
                continue
            f_act = "SELL" if sig.direction > 0 else "BUY"
            s_act = "BUY" if sig.direction > 0 else "SELL"
            f_sign = 1 if f_act == "BUY" else -1
            package = {fut: f_sign, self.spot: -f_sign * ratio}
            q = min(self.sized(clip), int(self.p.get("carry_max", 100)) - abs(fpos),
                    self.risk.room_package(package, positions, cap=clip))
            if q <= 0:
                continue
            log.info("CARRY %s mispricing %.3f -> %s %d %s / %s %d %s", fut, sig.mispricing,
                     f_act, q, fut, s_act, int(q * ratio), self.spot)
            got = self._pair(fut, f_act, q, s_act, int(q * ratio), snap, positions)
            self.carry_pos[fut] = fpos + f_sign * got

    def _pair(self, fut: str, f_act: str, fq: int, s_act: str, sq: int, snap: Snapshot,
              positions: dict[str, int]) -> int:
        """
        Send both legs, book each leg's FILLS into `positions` and return the futures
        contracts filled. Any leg mismatch is squared by rehedge_spot.
        """
        slip = self.p.get("carry_slippage", 0.02)
        fb, fa = snap.quote(fut)
        sb, sa = snap.quote(self.spot)
        if None in (fb, fa, sb, sa) or fq <= 0:
            return 0
        # Both legs at once: the gap between legs is unhedged basis risk.
        res = self.ex.limit_many([(fut, f_act, fq, fa + slip if f_act == "BUY" else fb - slip),
                                  (self.spot, s_act, sq, sa + slip if s_act == "BUY" else sb - slip)])
        if len(res) < 2:
            return 0
        f_got, s_got = self.ex.filled(res[0], fq), self.ex.filled(res[1], sq)
        positions[fut] = positions.get(fut, 0) + (f_got if f_act == "BUY" else -f_got)
        positions[self.spot] = positions.get(self.spot, 0) + (s_got if s_act == "BUY" else -s_got)
        return f_got

    def learn_impact(self, snap: Snapshot, now: int) -> None:
        """Score each report's actual spot reaction `impact_learn_ticks` later."""
        lag = int(self.p.get("impact_learn_ticks", 6))
        mid = snap.mid(self.spot)
        keep = []
        for tick, m0, x in self.pending_obs:
            if now - tick < lag or mid is None:
                keep.append((tick, m0, x))
                continue
            before = self.impact.mean
            self.impact.update(x, mid - m0)
            log.info("IMPACT learned from move %+.3f on x %+.2f: %.3f -> %.3f (sd %.3f, n=%d)",
                     mid - m0, x, before, self.impact.mean, self.impact.sd, self.impact.n)
        self.pending_obs = keep

    def run_news(self, snap: Snapshot) -> None:
        now = self.abs_tick(snap)
        bayes = self.p.get("impact_mode", "fixed") == "bayes"
        if bayes:
            self.learn_impact(snap, now)
        for item in self.new_news():
            inv = parse_inventory_news(f"{item.get('headline', '')} {item.get('body', '')}")
            if inv is None:
                continue
            spot_mid = snap.mid(self.spot)
            if bayes and spot_mid is not None:
                self.pending_obs.append((now, spot_mid, -inv.surprise))
            impact = self.impact.mean if bayes else self.p.get("impact_per_unit", 0.25)
            size, move = news_trade(inv.surprise, impact,
                                    self.p.get("news_threshold", 0.10), self.sized(self.p.get("news_max_size", 30)),
                                    self.p.get("news_full_move", 0.75))
            tgt = self.front(snap)
            log.info("NEWS inventory actual %+.2f exp %s surprise %+.2f -> move %+.3f size %+d on %s",
                     inv.actual, inv.expected, inv.surprise, move, size, tgt)
            if not size or tgt is None:
                continue
            action = "BUY" if size > 0 else "SELL"
            qty = min(abs(size), self.risk.room(tgt, action, snap.positions))
            bid, ask = snap.quote(tgt)
            if qty <= 0 or bid is None or ask is None:
                continue
            # Pay up to a third of the expected move to get in first.
            px = ask + abs(move) / 3 if action == "BUY" else bid - abs(move) / 3
            got = self.ex.filled(self.ex.limit(tgt, action, qty, px), qty)
            if got <= 0:
                continue          # missed the move: there is nothing to exit later
            entry = ask if action == "BUY" else bid
            self.news_pos.append(NewsPosition(tgt, got if size > 0 else -got, now, entry, move))

        hold = int(self.p.get("news_hold_ticks", 15))
        keep = []
        for npos in self.news_pos:
            mid = snap.mid(npos.ticker)
            realised = (mid - npos.entry_px) if mid is not None else 0.0
            progress = realised / npos.target_move if npos.target_move else 0.0
            stop = self.p.get("news_stop", 0.0)
            # Stop-loss: the market moved AGAINST the surprise by `news_stop` x the expected
            # move - the read was wrong (or already priced), so don't hold it to the timer.
            done = (now - npos.entry_tick >= hold or progress >= self.p.get("news_take_profit", 0.8)
                    or (stop > 0 and progress <= -stop))
            if not done:
                keep.append(npos)
                continue
            bid, ask = snap.quote(npos.ticker)
            action = "SELL" if npos.qty > 0 else "BUY"
            if bid is None or ask is None:
                keep.append(npos)
                continue
            px = bid - 0.05 if action == "SELL" else ask + 0.05
            log.info("NEWS exit %s %d %s (realised %+.3f of %+.3f)", action, abs(npos.qty), npos.ticker,
                     realised, npos.target_move)
            self.ex.limit(npos.ticker, action, abs(npos.qty), px)
        self.news_pos = keep
