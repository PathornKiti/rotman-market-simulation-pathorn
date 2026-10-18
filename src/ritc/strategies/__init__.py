"""
Strategy registry. One module per RITC case:

    liquidity    tender offers: accept profitable blocks, unwind them cheaply
"""

from .liquidity import LiquidityStrategy

REGISTRY = {"liquidity": LiquidityStrategy}

__all__ = ["REGISTRY", "LiquidityStrategy"]
