"""
Risk limits. RIT fines you for every unit over a gross/net limit, so the cheapest
risk control is never sending the order that breaches one.

`RiskManager.room()` answers: "how many more units of TICKER can I BUY/SELL
right now without breaching any configured limit?" Strategies clip every order
to that number.

Limit groups mirror RIT's `/limits` endpoint, where a single limit can cover
several tickers with different weights (e.g. an ETF that counts double):

    [risk.groups.equity]
    gross = 250000
    net = 100000
    weights = { RITC = 2.0, COMP = 1.0 }

or, for many tickers at once (e.g. every option), a regex:

    [risk.groups.options]
    gross = 2500
    net = 1000
    pattern = '^RTM\\d[CP]'
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class LimitGroup:
    name: str
    gross: float
    net: float
    weights: dict[str, float]
    pattern: str = ""             # regex: matching tickers get `pattern_weight`
    pattern_weight: float = 1.0

    def weight(self, ticker: str) -> float:
        if ticker in self.weights:
            return self.weights[ticker]
        if self.pattern and re.search(self.pattern, ticker):
            return self.pattern_weight
        return 0.0

    def exposure(self, positions: dict[str, int]) -> tuple[float, float]:
        gross = sum(abs(q) * self.weight(t) for t, q in positions.items())
        net = sum(q * self.weight(t) for t, q in positions.items())
        return gross, net


@dataclass
class RiskManager:
    max_position: dict[str, int] = field(default_factory=dict)
    groups: list[LimitGroup] = field(default_factory=list)
    buffer: float = 0.98          # use at most 98% of a limit - fills can overshoot
    halted: bool = False

    @classmethod
    def from_config(cls, cfg: dict) -> RiskManager:
        r = cfg.get("risk", {})
        groups = [
            LimitGroup(name, float(g.get("gross", 1e18)), float(g.get("net", 1e18)),
                       {k: float(v) for k, v in g.get("weights", {}).items()},
                       g.get("pattern", ""), float(g.get("pattern_weight", 1.0)))
            for name, g in r.get("groups", {}).items()
        ]
        return cls(
            max_position={k: int(v) for k, v in r.get("max_position", {}).items()},
            groups=groups,
            buffer=float(r.get("buffer", 0.98)),
        )

    def update_from_api(self, limits: list[dict], ticker_weights: dict[str, dict[str, float]] | None = None) -> None:
        """Tighten configured groups to whatever the live server reports."""
        by_name = {g.name: g for g in self.groups}
        for row in limits:
            name = row.get("name", "")
            g = by_name.get(name)
            if g is None and ticker_weights and name in ticker_weights:
                g = LimitGroup(name, 1e18, 1e18, ticker_weights[name])
                self.groups.append(g)
                by_name[name] = g
            if g is not None:
                if row.get("gross_limit"):
                    g.gross = min(g.gross, float(row["gross_limit"]))
                if row.get("net_limit"):
                    g.net = min(g.net, float(row["net_limit"]))

    def room(self, ticker: str, action: str, positions: dict[str, int]) -> int:
        """Max additional units of `ticker` we can trade in `action` direction."""
        if self.halted:
            return 0
        sign = 1 if action.upper() == "BUY" else -1
        pos = positions.get(ticker, 0)
        best = float("inf")

        cap = self.max_position.get(ticker)
        if cap is not None:
            best = min(best, max(0, cap * self.buffer - sign * pos))

        for g in self.groups:
            w = g.weight(ticker)
            if not w:
                continue
            gross, net = g.exposure(positions)
            gross_lim, net_lim = g.gross * self.buffer, g.net * self.buffer
            # Net: moving in `sign` direction changes net by sign*w per unit.
            best = min(best, max(0.0, (net_lim - sign * net) / w))
            # Gross: trading toward zero reduces gross, so only binds past zero.
            if sign * pos >= 0:
                best = min(best, max(0.0, (gross_lim - gross) / w))
            else:
                best = min(best, abs(pos) + max(0.0, (gross_lim - gross) / w))
        return 10**9 if best == float("inf") else int(best)

    def within(self, positions: dict[str, int]) -> bool:
        for t, cap in self.max_position.items():
            if abs(positions.get(t, 0)) > cap:
                return False
        for g in self.groups:
            gross, net = g.exposure(positions)
            if gross > g.gross or abs(net) > g.net:
                return False
        return True
