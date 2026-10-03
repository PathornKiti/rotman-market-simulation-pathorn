"""
Black-Scholes pricing, Greeks and implied volatility - stdlib only (no scipy).

All times are in YEARS. RIT cases usually quote expiry in ticks/periods; convert
with `ticks_to_years(ticks_left, ticks_per_year)` using the convention stated in
the case brief (e.g. 1 month = 1 period = 300 ticks -> ticks_per_year = 3600).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SQRT_2PI = math.sqrt(2 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def ticks_to_years(ticks: float, ticks_per_year: float) -> float:
    return max(ticks, 0.0) / ticks_per_year


def _d1d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vt
    return d1, d1 - vt


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    if T <= 0 or sigma <= 0:
        intrinsic = S - K if is_call else K - S
        return max(intrinsic, 0.0)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    if is_call:
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    vega: float      # per 1.00 change in vol (divide by 100 for per vol-point)
    theta: float     # per year


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> Greeks:
    if T <= 0 or sigma <= 0:
        price = bs_price(S, K, 0, r, sigma, is_call)
        itm = (S > K) if is_call else (S < K)
        delta = (1.0 if is_call else -1.0) if itm else 0.0
        return Greeks(price, delta, 0.0, 0.0, 0.0)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    sqrt_t = math.sqrt(T)
    pdf = norm_pdf(d1)
    disc = math.exp(-r * T)
    price = bs_price(S, K, T, r, sigma, is_call)
    delta = norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0
    gamma = pdf / (S * sigma * sqrt_t)
    vega = S * pdf * sqrt_t
    if is_call:
        theta = -S * pdf * sigma / (2 * sqrt_t) - r * K * disc * norm_cdf(d2)
    else:
        theta = -S * pdf * sigma / (2 * sqrt_t) + r * K * disc * norm_cdf(-d2)
    return Greeks(price, delta, gamma, vega, theta)


def implied_vol(price: float, S: float, K: float, T: float, r: float, is_call: bool,
                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6) -> float | None:
    """
    Newton with a bisection fallback. Returns None when the price is outside
    no-arbitrage bounds (below intrinsic / above the underlying) - those quotes
    are an arbitrage on their own, not a vol signal.
    """
    if T <= 0 or price <= 0:
        return None
    disc_k = K * math.exp(-r * T)
    lower = max(S - disc_k, 0.0) if is_call else max(disc_k - S, 0.0)
    upper = S if is_call else disc_k
    if price < lower - 1e-9 or price > upper + 1e-9:
        return None

    sigma = 0.3
    for _ in range(50):
        g = bs_greeks(S, K, T, r, sigma, is_call)
        diff = g.price - price
        if abs(diff) < tol:
            return sigma
        if g.vega < 1e-8:
            break
        nxt = sigma - diff / g.vega
        if not (lo < nxt < hi):
            break
        sigma = nxt

    a, b = lo, hi
    for _ in range(200):
        m = 0.5 * (a + b)
        if bs_price(S, K, T, r, m, is_call) > price:
            b = m
        else:
            a = m
        if b - a < tol:
            break
    return 0.5 * (a + b)


def put_call_parity_gap(call: float, put: float, S: float, K: float, T: float, r: float) -> float:
    """C - P - (S - K e^{-rT}). Non-zero beyond transaction costs = free money."""
    return call - put - (S - K * math.exp(-r * T))
