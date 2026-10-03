"""
Order-book analytics. Pure computation on API payloads - nothing here sends orders.

The single most important fact about RIT execution: a market order WALKS the
book. Your fill price is the VWAP across every level you consume, not the
touch. Every strategy in this repo sizes trades with `walk()` so it never pays
back its edge as slippage.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Level:
    price: float
    quantity: int
    trader_id: str = ""


@dataclass(frozen=True)
class Fill:
    """Result of walking the book for `requested` units."""
    requested: int
    filled: int
    vwap: float
    worst: float
    touch: float

    @property
    def complete(self) -> bool:
        return self.filled >= self.requested

    @property
    def slippage(self) -> float:
        return abs(self.vwap - self.touch) if self.filled else 0.0


@dataclass
class OrderBook:
    ticker: str
    bids: list[Level] = field(default_factory=list)   # best (highest) first
    asks: list[Level] = field(default_factory=list)   # best (lowest) first

    @classmethod
    def from_api(cls, payload: dict, ticker: str = "", exclude_trader: str | None = None) -> OrderBook:
        """
        Accepts both the live API's singular keys ({"bid", "ask"}) and plural ones.
        `exclude_trader` removes our own resting orders so we never trade with ourselves.
        """
        def build(rows: list[dict] | None, reverse: bool) -> list[Level]:
            out = []
            for r in rows or []:
                if r.get("price") is None:
                    continue
                avail = int(r.get("quantity", 0)) - int(r.get("quantity_filled", 0) or 0)
                tid = str(r.get("trader_id", ""))
                if avail <= 0 or (exclude_trader and tid == exclude_trader):
                    continue
                out.append(Level(float(r["price"]), avail, tid))
            out.sort(key=lambda lv: lv.price, reverse=reverse)
            return out

        bids = payload.get("bid", payload.get("bids"))
        asks = payload.get("ask", payload.get("asks"))
        return cls(ticker=ticker, bids=build(bids, True), asks=build(asks, False))

    # ------------------------------------------------------------ top of book
    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return self.best_bid if self.best_ask is None else self.best_ask
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def microprice(self, levels: int = 1) -> float | None:
        """Size-weighted mid: leans toward the side about to be exhausted."""
        if not self.bids or not self.asks:
            return self.mid
        bq = sum(lv.quantity for lv in self.bids[:levels])
        aq = sum(lv.quantity for lv in self.asks[:levels])
        if bq + aq == 0:
            return self.mid
        return (self.best_bid * aq + self.best_ask * bq) / (bq + aq)

    def imbalance(self, levels: int = 3) -> float:
        """(bid depth - ask depth) / total, in [-1, 1]. Positive = buying pressure."""
        bq = sum(lv.quantity for lv in self.bids[:levels])
        aq = sum(lv.quantity for lv in self.asks[:levels])
        return 0.0 if bq + aq == 0 else (bq - aq) / (bq + aq)

    def depth(self, side: str, limit_price: float | None = None) -> int:
        """Total size on `side` ('bid'/'ask') at prices no worse than limit_price."""
        levels = self.bids if side == "bid" else self.asks
        total = 0
        for lv in levels:
            if limit_price is not None:
                if side == "bid" and lv.price < limit_price:
                    break
                if side == "ask" and lv.price > limit_price:
                    break
            total += lv.quantity
        return total

    # ------------------------------------------------------------------ walk
    def walk(self, action: str, quantity: int) -> Fill:
        """Simulate a market order: BUY consumes asks, SELL consumes bids."""
        levels = self.asks if action.upper() == "BUY" else self.bids
        touch = levels[0].price if levels else 0.0
        left, cost, worst = int(quantity), 0.0, touch
        for lv in levels:
            if left <= 0:
                break
            take = min(left, lv.quantity)
            cost += take * lv.price
            worst = lv.price
            left -= take
        filled = int(quantity) - left
        return Fill(int(quantity), filled, cost / filled if filled else 0.0, worst, touch)

    def max_qty_within(self, action: str, limit_vwap: float, cap: int) -> int:
        """
        Largest size whose *blended* VWAP is no worse than `limit_vwap`.
        This is how every bot sizes an aggressive trade.
        """
        levels = self.asks if action.upper() == "BUY" else self.bids
        sign = 1 if action.upper() == "BUY" else -1
        qty, cost = 0, 0.0
        for lv in levels:
            if qty >= cap:
                break
            # Room left at this level so blended VWAP stays within the limit:
            # sign*(cost + x*p) <= sign*limit*(qty + x)  ->  x*sign*(p-limit) <= sign*(limit*qty - cost)
            edge = sign * (lv.price - limit_vwap)
            if edge <= 0:
                take = lv.quantity
            else:
                slack = sign * (limit_vwap * qty - cost)
                take = int(slack // edge) if slack > 0 else 0
            take = min(take, lv.quantity, cap - qty)
            if take <= 0:
                break
            qty += take
            cost += take * lv.price
        return qty
