"""
News parsing helpers. RIT news is free text, so every parser here:

* returns None instead of guessing when it cannot read an item, and
* is driven by regexes you can extend in config without touching code.

Always print a few real headlines with `python -m ritc doctor` on the day and
check that the parsers below hit them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PCT = r"(-?\d+(?:\.\d+)?)\s*%"


@dataclass(frozen=True)
class VolNews:
    realized: float | None = None          # annualised, as a fraction (0.20 = 20%)
    forecast_lo: float | None = None
    forecast_hi: float | None = None
    delta_limit: int | None = None
    penalty_pct: float | None = None       # delta-limit penalty rate, as a fraction (0.005 = 0.5%)

    @property
    def forecast_mid(self) -> float | None:
        if self.forecast_lo is None or self.forecast_hi is None:
            return None
        return (self.forecast_lo + self.forecast_hi) / 2


def parse_vol_news(text: str) -> VolNews:
    """
    Understands the RITC volatility-case phrasing (official wording from the case packages):
      "The realized volatility of RTM for this week will be 22%"
      "The realized volatility of RTM for next week will be between 27-30%"   (official)
      "...volatility ... next week will be between 18% and 24%"
      "The delta limit for this heat is 5,000 and the penalty percentage is 0.5%"
    """
    t = " ".join(text.split())
    realized = lo = hi = None
    delta = penalty = None

    m = re.search(r"between\s+(-?\d+(?:\.\d+)?)\s*%?\s*(?:-|–|to|and)\s*" + _PCT, t, re.I)
    if m:
        lo, hi = float(m.group(1)) / 100, float(m.group(2)) / 100
    m = re.search(r"(?:realized|annuali[sz]ed)\s+volatility[^%]*?(?:will be|is|of)\s+" + _PCT, t, re.I)
    if m and lo is None:
        realized = float(m.group(1)) / 100
    m = re.search(r"delta\s+limit[^\d]*([\d,]+)", t, re.I)
    if m:
        delta = int(m.group(1).replace(",", ""))
    m = re.search(r"penalty\s+percentage[^\d]*" + _PCT, t, re.I)
    if m:
        penalty = float(m.group(1)) / 100
    return VolNews(realized, lo, hi, delta, penalty)


@dataclass(frozen=True)
class InventoryNews:
    actual: float          # positive = build, negative = draw (in millions of units)
    expected: float | None

    @property
    def surprise(self) -> float:
        return self.actual - (self.expected or 0.0)


_NUM = r"(\d+(?:\.\d+)?)"


def parse_inventory_news(text: str) -> InventoryNews | None:
    """
    Commodity inventory reports, e.g.
      "Crude inventories rose by 2.5 million barrels vs expected build of 1.0 million"
      "Weekly report: a DRAW of 3 million barrels. Analysts expected a draw of 1.5 million"
    Builds are bearish for spot, draws bullish. Returns None if not an inventory item.
    """
    t = " ".join(text.split()).lower()
    if not any(w in t for w in ("inventor", "stockpile", "storage", "build", "draw")):
        return None

    def signed(word: str, val: str) -> float:
        bearish = any(w in word for w in ("build", "rose", "increase", "rise", "up"))
        return float(val) if bearish else -float(val)

    pat = r"(build|draw|rose|fell|increase[d]?|decrease[d]?|rise|drop(?:ped)?|up|down)\w*\s+(?:of\s+|by\s+)?" + _NUM
    hits = re.findall(pat, t)
    if not hits:
        return None
    actual = signed(*hits[0])
    expected = None
    exp_pat = r"(?:expect\w*|forecast\w*|consensus)[^\d]*?(build|draw|increase|decrease|rise|drop)?\w*[^\d]*"
    m = re.search(exp_pat + _NUM, t)
    if m:
        expected = signed(m.group(1) or hits[0][0], m.group(2))
    return InventoryNews(actual, expected)


def first_number(text: str) -> float | None:
    m = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)
    return float(m.group(0).replace(",", "")) if m else None


def parse_position_limit(text: str) -> int | None:
    """
    The market-making case's aggregate position limit, announced by news at the start of a heat
    (RITC 2026: |SPNG| + |SMMR| + |ATMN| + |WNTR| at every market close). The exact wording is not
    published, so: any item that mentions a position limit (not a delta limit) gives its largest
    share count of at least 1,000, e.g. "The aggregate position limit for this week is 15,000 shares".
    """
    t = " ".join(text.split())
    if not re.search(r"position\s+limit|limit\s+on\s+(?:your\s+)?(?:aggregate\s+)?position", t, re.I) \
            or re.search(r"delta\s+limit", t, re.I):
        return None
    sizes = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", t)]
    sizes = [n for n in sizes if n >= 1000]
    return max(sizes) if sizes else None
