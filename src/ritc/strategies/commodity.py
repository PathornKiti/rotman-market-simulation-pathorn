"""
COMMODITY TRADING - cost-of-carry arbitrage + inventory-news momentum.

Two independent engines, both on a spot commodity and its futures:

1. Carry arbitrage (low risk)
   A future should trade at  F = S + carry_per_tick * ticks_to_expiry
   (storage + financing). When the executable basis strays from that by more
   than costs + `carry_edge`:
       F too rich -> SELL future, BUY spot  (cash-and-carry)
       F too cheap -> BUY future, SELL spot (reverse; needs shortable spot)
   The same logic runs on the spread between two futures (calendar spread).
   The trade is closed once the basis converges inside `carry_exit`.

2. News momentum (directional, sized small)
   Inventory reports move price: a bigger-than-expected BUILD is bearish, a
   bigger DRAW is bullish. RIT prices adjust over several ticks, so we trade
   the front future immediately in the surprise direction and exit after
   `news_hold_ticks` (or earlier if price already moved `news_target`).
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
class NewsPosition:
    ticker: str
    qty: int
    entry_tick: int
    entry_px: float
    target_move: float


class CommodityStrategy(Strategy):
    name = "commodity"

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
        self.last_news = 0
        self.news_pos: list[NewsPosition] = []
        # Futures held by the carry engine only (news trades are tracked separately),
        # so a converged-basis exit never closes a news position by mistake.
        self.carry_pos: dict[str, int] = {}

    # ----------------------------------------------------------------- helpers
    def abs_tick(self, snap: Snapshot) -> int:
        return (snap.period - 1) * snap.ticks_per_period + snap.tick

    def front(self, snap: Snapshot) -> str | None:
        now = self.abs_tick(snap)
        live = [(exp, t) for t, exp in self.futures.items() if exp > now + 5 and t in snap.securities]
        return min(live)[1] if live else None

    # -------------------------------------------------------------------- step
    def step(self, snap: Snapshot) -> None:
        self.run_news(snap)
        self.run_carry(snap)

    def run_carry(self, snap: Snapshot) -> None:
        now = self.abs_tick(snap)
        positions = snap.positions
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
                self._pair(fut, f_act, q, s_act, int(q * ratio), snap)
                self.carry_pos[fut] = fpos + (q if f_act == "BUY" else -q)
                continue

            if sig.direction == 0:
                continue
            # Don't pyramid past the configured carry size on one future.
            if abs(fpos) >= int(self.p.get("carry_max", 100)):
                continue
            f_act = "SELL" if sig.direction > 0 else "BUY"
            s_act = "BUY" if sig.direction > 0 else "SELL"
            q = min(clip, self.risk.room(fut, f_act, positions),
                    int(self.risk.room(self.spot, s_act, positions) / ratio))
            if q <= 0:
                continue
            log.info("CARRY %s mispricing %.3f -> %s %d %s / %s %d %s", fut, sig.mispricing,
                     f_act, q, fut, s_act, int(q * ratio), self.spot)
            self._pair(fut, f_act, q, s_act, int(q * ratio), snap)
            signed = q if f_act == "BUY" else -q
            positions[fut] = positions.get(fut, 0) + signed
            self.carry_pos[fut] = fpos + signed

    def _pair(self, fut: str, f_act: str, fq: int, s_act: str, sq: int, snap: Snapshot) -> None:
        slip = self.p.get("carry_slippage", 0.02)
        fb, fa = snap.quote(fut)
        sb, sa = snap.quote(self.spot)
        if None in (fb, fa, sb, sa):
            return
        self.ex.limit(fut, f_act, fq, fa + slip if f_act == "BUY" else fb - slip)
        self.ex.limit(self.spot, s_act, sq, sa + slip if s_act == "BUY" else sb - slip)

    def run_news(self, snap: Snapshot) -> None:
        now = self.abs_tick(snap)
        for item in sorted(self.client.news(since=self.last_news), key=lambda n: n.get("news_id", 0)):
            nid = int(item.get("news_id", 0))
            if nid <= self.last_news:
                continue
            self.last_news = nid
            inv = parse_inventory_news(f"{item.get('headline', '')} {item.get('body', '')}")
            if inv is None:
                continue
            size, move = news_trade(inv.surprise, self.p.get("impact_per_unit", 0.25),
                                    self.p.get("news_threshold", 0.10), int(self.p.get("news_max_size", 30)),
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
            self.ex.limit(tgt, action, qty, px)
            entry = ask if action == "BUY" else bid
            self.news_pos.append(NewsPosition(tgt, qty if size > 0 else -qty, now, entry, move))

        hold = int(self.p.get("news_hold_ticks", 15))
        keep = []
        for npos in self.news_pos:
            mid = snap.mid(npos.ticker)
            realised = (mid - npos.entry_px) if mid is not None else 0.0
            done = now - npos.entry_tick >= hold or (
                npos.target_move and realised / npos.target_move >= self.p.get("news_take_profit", 0.8))
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
