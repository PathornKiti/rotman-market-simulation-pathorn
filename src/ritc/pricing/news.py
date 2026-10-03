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

    @property
    def forecast_mid(self) -> float | None:
        if self.forecast_lo is None or self.forecast_hi is None:
            return None
        return (self.forecast_lo + self.forecast_hi) / 2


def parse_vol_news(text: str) -> VolNews:
    """
    Understands the usual RITC volatility-case phrasing, e.g.
      "The realized volatility of RTM for this week will be 22%"
      "...volatility ... next week will be between 18% and 24%"
      "The delta limit for this sub-heat is 7,000 ..."
    """
    t = " ".join(text.split())
    realized = lo = hi = None
    delta = None

    m = re.search(r"between\s+" + _PCT + r"\s+and\s+" + _PCT, t, re.I)
    if m:
        lo, hi = float(m.group(1)) / 100, float(m.group(2)) / 100
    m = re.search(r"(?:realized|annuali[sz]ed)\s+volatility[^%]*?(?:will be|is|of)\s+" + _PCT, t, re.I)
    if m and "between" not in m.group(0).lower():
        realized = float(m.group(1)) / 100
    m = re.search(r"delta\s+limit[^\d]*([\d,]+)", t, re.I)
    if m:
        delta = int(m.group(1).replace(",", ""))
    return VolNews(realized, lo, hi, delta)


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
