"""
Transaction-cost analysis: did we get the price we thought we would?

Every fill is compared with the ARRIVAL price - the mid when the decision was
made. Implementation shortfall per unit:

    shortfall = sign * (fill VWAP - arrival mid)       # > 0 = cost, < 0 = we earned the spread

Aggregated per ticker and per order style (aggressive IOC vs passive), this
tells you which bot leaks money in execution, and whether passive posting is
actually earning the spread it is supposed to. Printed at shutdown and written
to the log.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class FillRecord:
    ticker: str
    action: str
    qty: int
    vwap: float
    arrival: float
    style: str          # "aggressive" | "passive"

    @property
    def shortfall(self) -> float:
        sign = 1 if self.action.upper() == "BUY" else -1
        return sign * (self.vwap - self.arrival)


@dataclass
class _Agg:
    qty: int = 0
    cost: float = 0.0          # sum(qty * shortfall)
    orders: int = 0

    @property
    def per_unit(self) -> float:
        return self.cost / self.qty if self.qty else 0.0


class TCA:
    def __init__(self) -> None:
        self.fills: list[FillRecord] = []
        self.sent: dict[tuple[str, str], int] = defaultdict(int)     # (ticker, style) -> qty sent

    def record_sent(self, ticker: str, qty: int, style: str) -> None:
        self.sent[(ticker, style)] += qty

    def record_fill(self, ticker: str, action: str, qty: int, vwap: float | None,
                    arrival: float | None, style: str) -> None:
        if qty <= 0 or vwap is None or arrival is None:
            return
        self.fills.append(FillRecord(ticker, action, int(qty), float(vwap), float(arrival), style))

    def summary(self) -> dict[tuple[str, str], _Agg]:
        out: dict[tuple[str, str], _Agg] = defaultdict(_Agg)
        for f in self.fills:
            a = out[(f.ticker, f.style)]
            a.qty += f.qty
            a.cost += f.qty * f.shortfall
            a.orders += 1
        return dict(out)

    def total_cost(self) -> float:
        return sum(f.qty * f.shortfall for f in self.fills)

    def report(self) -> str:
        s = self.summary()
        if not s:
            return "TCA: no fills recorded"
        lines = ["TCA (shortfall vs arrival mid; + = cost, - = earned)",
                 f"{'ticker':<10}{'style':<12}{'filled':>10}{'fill%':>8}{'$/unit':>10}{'total $':>12}"]
        for (t, style), a in sorted(s.items()):
            sent = self.sent.get((t, style), 0)
            fill_pct = 100 * a.qty / sent if sent else 0.0
            lines.append(f"{t:<10}{style:<12}{a.qty:>10}{fill_pct:>7.0f}%{a.per_unit:>10.4f}{a.cost:>12,.2f}")
        lines.append(f"{'TOTAL':<22}{sum(a.qty for a in s.values()):>10}{'':>8}{'':>10}{self.total_cost():>12,.2f}")
        return "\n".join(lines)
