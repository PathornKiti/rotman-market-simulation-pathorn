"""Small online statistics used for adaptive spreads and signals."""

from __future__ import annotations

import math
from collections import deque


class EWMA:
    """Exponentially weighted mean and variance. `halflife` is in updates."""

    def __init__(self, halflife: float = 20.0):
        self.alpha = 1 - math.exp(math.log(0.5) / max(halflife, 1e-9))
        self.mean: float | None = None
        self.var = 0.0

    def update(self, x: float) -> float:
        if self.mean is None:
            self.mean = x
            return x
        d = x - self.mean
        self.mean += self.alpha * d
        self.var = (1 - self.alpha) * (self.var + self.alpha * d * d)
        return self.mean

    @property
    def std(self) -> float:
        return math.sqrt(self.var)


class ReturnVol:
    """EWMA volatility of tick-to-tick price changes (absolute, not log)."""

    def __init__(self, halflife: float = 30.0):
        self.ewma = EWMA(halflife)
        self.last: float | None = None

    def update(self, price: float | None) -> float:
        if price is None:
            return self.std
        if self.last is not None:
            self.ewma.update(price - self.last)
        self.last = price
        return self.std

    @property
    def std(self) -> float:
        return self.ewma.std


class RollingZ:
    """Rolling z-score over the last `window` observations."""

    def __init__(self, window: int = 60):
        self.buf: deque[float] = deque(maxlen=window)

    def update(self, x: float) -> float:
        self.buf.append(x)
        return self.z(x)

    def z(self, x: float) -> float:
        n = len(self.buf)
        if n < max(5, self.buf.maxlen // 4 if self.buf.maxlen else 5):
            return 0.0
        m = sum(self.buf) / n
        v = sum((b - m) ** 2 for b in self.buf) / (n - 1)
        return 0.0 if v <= 1e-12 else (x - m) / math.sqrt(v)

    @property
    def mean(self) -> float:
        return sum(self.buf) / len(self.buf) if self.buf else 0.0
