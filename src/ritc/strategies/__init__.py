"""
Strategy registry. One module per RITC case family:

    liability    tender offers: accept profitable blocks, unwind them cheaply
    derivatives  options volatility arbitrage, delta-hedged
    etf          ETF vs basket arbitrage
    equity       inventory-aware market making
    commodity    futures cost-of-carry arbitrage + inventory-news momentum
"""

from .commodity import CommodityStrategy
from .derivatives import DerivativesStrategy
from .equity import EquityStrategy
from .etf import ETFStrategy
from .liability import LiabilityStrategy

REGISTRY = {
    "liability": LiabilityStrategy,
    "derivatives": DerivativesStrategy,
    "etf": ETFStrategy,
    "equity": EquityStrategy,
    "commodity": CommodityStrategy,
}

__all__ = ["REGISTRY", "CommodityStrategy", "DerivativesStrategy", "ETFStrategy",
           "EquityStrategy", "LiabilityStrategy"]
