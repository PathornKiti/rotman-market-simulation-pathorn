"""
Time-series models for prices: volatility through time (GARCH) and value through
time (Kalman level + Ornstein-Uhlenbeck mean reversion). Stdlib only.

Why these
---------
* **GARCH(1,1)** - volatility clusters: a jumpy tick is usually followed by more
  jumpy ticks. GARCH forecasts the variance of the NEXT tick and of any horizon
  ahead (e.g. until an option expires), mean-reverting toward a long-run level:

      sigma2[t+1] = omega + alpha * r[t]^2 + beta * sigma2[t]
      E[sigma2[t+k]] = VL + (alpha+beta)^(k-1) * (sigma2[t+1] - VL),  VL = omega/(1-alpha-beta)

  Fitted by maximum likelihood with variance targeting (omega pinned so the
  long-run variance equals the sample variance), which makes the fit stable on
  the few hundred observations a RIT case gives you.

* **Kalman local level** - the "true" price is a random walk hidden under bid/ask
  bounce and noise. The filter gives a denoised fair value and its uncertainty.

* **Ornstein-Uhlenbeck** - if a price mean-reverts, x[t+1] = a + b*x[t] + e is an
  AR(1) with b < 1. Long-run mean mu = a/(1-b), half-life = ln2 / -ln(b). The
  expected price h ticks ahead is mu + (x - mu) * b^h. We only trust it when the
  fit is significant (t-stat on b-1) - most RIT stocks are random walks, and a
  spurious mean-reversion signal is worse than none.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass


# ------------------------------------------------------------------ helpers
def log_returns(prices: Sequence[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(prices, prices[1:]) if a > 0 and b > 0]


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def _logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def nelder_mead(f: Callable[[list[float]], float], x0: list[float], step: float = 0.5,
                iters: int = 400, tol: float = 1e-9) -> list[float]:
    """Minimal Nelder-Mead minimiser (enough for 2-3 parameters)."""
    n = len(x0)
    pts = [list(x0)] + [[x0[j] + (step if j == i else 0.0) for j in range(n)] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(n + 1), key=vals.__getitem__)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        centroid = [sum(p[j] for p in pts[:-1]) / n for j in range(n)]
        refl = [centroid[j] + (centroid[j] - pts[-1][j]) for j in range(n)]
        fr = f(refl)
        if fr < vals[0]:
            exp_ = [centroid[j] + 2 * (centroid[j] - pts[-1][j]) for j in range(n)]
            fe = f(exp_)
            pts[-1], vals[-1] = (exp_, fe) if fe < fr else (refl, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = refl, fr
        else:
            con = [centroid[j] + 0.5 * (pts[-1][j] - centroid[j]) for j in range(n)]
            fc = f(con)
            if fc < vals[-1]:
                pts[-1], vals[-1] = con, fc
            else:                                     # shrink toward the best point
                for i in range(1, n + 1):
                    pts[i] = [pts[0][j] + 0.5 * (pts[i][j] - pts[0][j]) for j in range(n)]
                    vals[i] = f(pts[i])
    return pts[min(range(n + 1), key=vals.__getitem__)]


def ljung_box(x: Sequence[float], lags: int = 5) -> float:
    """Ljung-Box Q statistic. Compare with the chi-square(lags) critical value (5 lags, 5%: 11.07)."""
    n = len(x)
    if n <= lags + 1:
        return 0.0
    m = sum(x) / n
    d = [v - m for v in x]
    c0 = sum(v * v for v in d)
    if c0 <= 0:
        return 0.0
    q = 0.0
    for k in range(1, lags + 1):
        rho = sum(d[i] * d[i - k] for i in range(k, n)) / c0
        q += rho * rho / (n - k)
    return n * (n + 2) * q


CHI2_95 = {1: 3.84, 2: 5.99, 3: 7.81, 4: 9.49, 5: 11.07, 10: 18.31}


def volatility_clusters(returns: Sequence[float], lags: int = 5) -> tuple[bool, float]:
    """
    ARCH-effect test: do squared returns autocorrelate? If yes, volatility clusters
    and GARCH has something to forecast; if not, a plain EWMA is as good or better.
    """
    q = ljung_box([r * r for r in returns], lags)
    return q > CHI2_95.get(lags, 11.07), q


# ------------------------------------------------------------------- GARCH
@dataclass
class Garch11:
    omega: float
    alpha: float
    beta: float
    mu: float = 0.0
    sigma2: float = 0.0          # conditional variance for the NEXT observation

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    @property
    def long_run_var(self) -> float:
        p = self.persistence
        return self.omega / (1 - p) if p < 1 else float("inf")

    @staticmethod
    def neg_loglik(returns: Sequence[float], omega: float, alpha: float, beta: float,
                   mu: float, s2_0: float) -> float:
        s2, nll = s2_0, 0.0
        for r in returns:
            e = r - mu
            s2 = max(s2, 1e-20)
            nll += 0.5 * (math.log(s2) + e * e / s2)
            s2 = omega + alpha * e * e + beta * s2
        return nll

    @classmethod
    def fit(cls, returns: Sequence[float], demean: bool = True) -> Garch11:
        """
        MLE with variance targeting. Parameterised as persistence p = alpha+beta in
        (0, 0.999) and alpha share in (0, 1), so every candidate is valid.
        """
        n = len(returns)
        if n < 20:
            raise ValueError("need at least 20 returns to fit GARCH")
        mu = sum(returns) / n if demean else 0.0
        var = sum((r - mu) ** 2 for r in returns) / n
        if var <= 0:
            return cls(0.0, 0.0, 0.0, mu, 0.0)

        def unpack(u: list[float]) -> tuple[float, float, float]:
            p = 0.999 * _sigmoid(u[0])
            share = _sigmoid(u[1])
            a, b = p * share, p * (1 - share)
            return var * (1 - p), a, b

        def obj(u: list[float]) -> float:
            w, a, b = unpack(u)
            return cls.neg_loglik(returns, w, a, b, mu, var)

        best = min(([_logit(p / 0.999), _logit(s)] for p in (0.5, 0.9, 0.97) for s in (0.1, 0.3)), key=obj)
        u = nelder_mead(obj, best, step=0.7)
        w, a, b = unpack(u)
        model = cls(w, a, b, mu, var)
        model.filter(returns)             # leave sigma2 at the latest conditional variance
        return model

    def filter(self, returns: Sequence[float]) -> list[float]:
        """Run the variance recursion; returns sigma2 for each observation."""
        s2 = self.sigma2 or self.long_run_var
        out = []
        for r in returns:
            out.append(s2)
            e = r - self.mu
            s2 = self.omega + self.alpha * e * e + self.beta * s2
        self.sigma2 = s2
        return out

    def update(self, r: float) -> float:
        e = r - self.mu
        self.sigma2 = self.omega + self.alpha * e * e + self.beta * self.sigma2
        return self.sigma2

    def forecast(self, h: int) -> list[float]:
        """Expected per-step variance for steps 1..h ahead."""
        vl, p = self.long_run_var, self.persistence
        return [vl + p ** (k - 1) * (self.sigma2 - vl) for k in range(1, max(h, 0) + 1)]

    def term_vol(self, h: int) -> float:
        """Average per-step volatility over the next h steps (sqrt of mean variance)."""
        if h <= 0:
            return math.sqrt(max(self.sigma2, 0.0))
        vl, p = self.long_run_var, self.persistence
        if p >= 1 or p <= 0:
            return math.sqrt(max(self.sigma2, 0.0))
        # closed form for sum_{k=1..h} p^(k-1) = (1 - p^h) / (1 - p)
        mean_var = vl + (self.sigma2 - vl) * (1 - p ** h) / ((1 - p) * h)
        return math.sqrt(max(mean_var, 0.0))


class OnlineGarch:
    """
    GARCH that learns while trading. Uses a RiskMetrics EWMA (lambda=0.94) until
    `min_obs` returns are in, then refits GARCH every `refit_every` observations on
    a rolling `window`, updating the conditional variance on every tick in between.
    """

    def __init__(self, window: int = 600, min_obs: int = 60, refit_every: int = 50, lam: float = 0.94):
        self.returns: deque[float] = deque(maxlen=window)
        self.min_obs = min_obs
        self.refit_every = refit_every
        self.lam = lam
        self.model: Garch11 | None = None
        self.ewma_var: float | None = None
        self.since_fit = 0
        self.last_price: float | None = None

    def add_price(self, price: float | None) -> None:
        if price is None or price <= 0:
            return
        if self.last_price is not None:
            self.add_return(math.log(price / self.last_price))
        self.last_price = price

    def add_return(self, r: float) -> None:
        self.returns.append(r)
        self.ewma_var = r * r if self.ewma_var is None else self.lam * self.ewma_var + (1 - self.lam) * r * r
        if self.model is not None:
            self.model.update(r)
        self.since_fit += 1
        if len(self.returns) >= self.min_obs and (self.model is None or self.since_fit >= self.refit_every):
            try:
                self.model = Garch11.fit(list(self.returns))
            except ValueError:
                pass
            self.since_fit = 0

    @property
    def ready(self) -> bool:
        return self.ewma_var is not None and len(self.returns) >= 10

    def vol(self) -> float:
        """Next-step volatility (log-return units)."""
        if self.model is not None:
            return math.sqrt(max(self.model.sigma2, 0.0))
        return math.sqrt(self.ewma_var or 0.0)

    def term_vol(self, h: int) -> float:
        """Average per-step volatility over the next h steps."""
        if self.model is not None:
            return self.model.term_vol(h)
        return self.vol()

    def annualised(self, h: int, steps_per_year: float) -> float:
        return self.term_vol(h) * math.sqrt(steps_per_year)


# ------------------------------------------------------- value through time
class KalmanLevel:
    """
    Local-level model:  level[t] = level[t-1] + w (var q);  price[t] = level[t] + v (var r).
    `q` is how much the true value moves per tick, `r` the observation noise
    (bid/ask bounce). Their ratio sets how quickly the estimate follows the price.
    If q/r are unknown, `adaptive=True` estimates them from the innovations.
    """

    def __init__(self, q: float = 1e-4, r: float = 1e-3, adaptive: bool = True, halflife: float = 50):
        self.q, self.r = q, r
        self.adaptive = adaptive
        self.alpha = 1 - 0.5 ** (1 / halflife)
        self.level: float | None = None
        self.p = 1.0
        self._d1_var: float | None = None      # var of first differences
        self._d1_cov: float = 0.0               # lag-1 autocovariance of differences
        self._last_obs: float | None = None
        self._last_diff: float | None = None

    def update(self, price: float | None) -> float | None:
        if price is None:
            return self.level
        if self.adaptive and self._last_obs is not None:
            d = price - self._last_obs
            if self._last_diff is not None:
                a = self.alpha
                self._d1_var = d * d if self._d1_var is None else (1 - a) * self._d1_var + a * d * d
                self._d1_cov = (1 - a) * self._d1_cov + a * d * self._last_diff
                # For a local level: var(diff) = q + 2r, cov(diff, lag diff) = -r.
                r = max(-self._d1_cov, 1e-12)
                q = max(self._d1_var - 2 * r, 1e-12)
                self.q, self.r = q, r
            self._last_diff = d
        self._last_obs = price
        if self.level is None:
            self.level, self.p = price, self.r
            return self.level
        p_pred = self.p + self.q
        k = p_pred / (p_pred + self.r)
        self.level += k * (price - self.level)
        self.p = (1 - k) * p_pred
        return self.level

    @property
    def std(self) -> float:
        return math.sqrt(max(self.p, 0.0))


@dataclass(frozen=True)
class OUFit:
    mu: float           # long-run mean
    b: float            # AR(1) coefficient per step
    sigma: float        # residual std per step
    t_stat: float       # t-statistic of (b - 1); strongly negative => real mean reversion
    n: int

    @property
    def kappa(self) -> float:
        return -math.log(self.b) if 0 < self.b < 1 else 0.0

    @property
    def half_life(self) -> float:
        return math.log(2) / self.kappa if self.kappa > 0 else float("inf")

    @property
    def stationary_std(self) -> float:
        return self.sigma / math.sqrt(1 - self.b * self.b) if abs(self.b) < 1 else float("inf")

    def expected(self, x: float, h: float) -> float:
        """E[x(t+h) | x(t) = x]."""
        if not 0 < self.b < 1:
            return x
        return self.mu + (x - self.mu) * self.b ** h

    def zscore(self, x: float) -> float:
        s = self.stationary_std
        return 0.0 if not math.isfinite(s) or s == 0 else (x - self.mu) / s

    def significant(self, t_crit: float = -3.5, max_half_life: float = 200) -> bool:
        """
        Dickey-Fuller-style check plus a usable half-life. Default -3.5 is roughly the 1%
        critical value: the model is refit every few ticks, so a 5% test would hand you a
        spurious mean-reversion signal on a pure random walk far too often.
        """
        return self.t_stat < t_crit and self.half_life <= max_half_life


def fit_ou(prices: Sequence[float]) -> OUFit | None:
    """OLS of x[t+1] on x[t]. Returns None when there is not enough data."""
    n = len(prices) - 1
    if n < 20:
        return None
    xs, ys = prices[:-1], prices[1:]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    s2 = sum(e * e for e in resid) / max(n - 2, 1)
    se_b = math.sqrt(s2 / sxx) if s2 > 0 else 1e-12
    t = (b - 1) / se_b
    mu = a / (1 - b) if abs(1 - b) > 1e-12 else my
    return OUFit(mu, b, math.sqrt(s2), t, n)


def ou_passage_time(theta: float, sigma: float, a: float, steps: int = 200) -> float:
    """
    Mean first-passage time of an OU process dX = -theta X dt + sigma dW from -a to +a
    (Bertram 2010, eq. for E[T]):

        E[T] = (2 / sigma^2) Int_{-a}^{a} exp(theta y^2 / sigma^2) Int_{-inf}^{y} exp(-theta z^2 / sigma^2) dz dy

    The inner integral is closed-form (erf); the outer one is Simpson's rule.
    """
    if theta <= 0 or sigma <= 0 or a <= 0:
        return float("inf")
    k = math.sqrt(theta) / sigma
    steps += steps % 2

    def f(y: float) -> float:
        inner = (math.sqrt(math.pi) / (2 * k)) * (1 + math.erf(k * y))
        return math.exp((k * y) ** 2) * inner

    h = 2 * a / steps
    tot = f(-a) + f(a) + sum((4 if i % 2 else 2) * f(-a + i * h) for i in range(1, steps))
    return (2 / sigma ** 2) * tot * h / 3


def bertram_band(theta: float, sigma: float, cost: float, grid: int = 60) -> tuple[float, float]:
    """
    Bertram (2010), "Analytic solutions for optimal statistical arbitrage trading":
    trade the OU spread long at -a and short at +a. Each hop between the bands earns
    2a - cost, and takes E[T](a), so the expected return PER UNIT TIME is

        mu(a) = (2a - cost) / E[T](a)

    A wide band earns more per trade but waits exponentially longer for the next one.
    Returns (a*, mu(a*)) maximising it; (0, 0) when no band beats the cost.
    `theta` is per step, `sigma` the per-step diffusion of the continuous OU.
    """
    if theta <= 0 or sigma <= 0:
        return 0.0, 0.0
    sd = sigma / math.sqrt(2 * theta)              # stationary std
    lo = max(cost / 2, 1e-9)
    best = (0.0, 0.0)
    for i in range(1, grid + 1):
        a = lo + (4 * sd - lo) * i / grid if 4 * sd > lo else lo * (1 + i / grid)
        mu = (2 * a - cost) / ou_passage_time(theta, sigma, a)
        if mu > best[1]:
            best = (a, mu)
    return best


def ou_continuous(fit: OUFit) -> tuple[float, float]:
    """(theta, sigma) per step of the continuous OU matching an AR(1) fit."""
    if not 0 < fit.b < 1:
        return 0.0, 0.0
    theta = -math.log(fit.b)
    return theta, fit.sigma * math.sqrt(2 * theta / (1 - fit.b * fit.b))


class FairValueModel:
    """
    Combines the two value-through-time views into one fair value per tick:

      * Kalman level   - always on: the denoised current value.
      * OU projection  - only when mean reversion is statistically significant:
                         where the price is expected to be `horizon` ticks ahead.

    fair = level, or (1 - ou_weight) * level + ou_weight * E_OU[price in `horizon`].
    """

    def __init__(self, window: int = 300, refit_every: int = 25, horizon: float = 10,
                 ou_weight: float = 0.5, t_crit: float = -3.5, max_half_life: float = 200):
        self.kalman = KalmanLevel()
        self.prices: deque[float] = deque(maxlen=window)
        self.refit_every = refit_every
        self.horizon = horizon
        self.ou_weight = ou_weight
        self.t_crit = t_crit
        self.max_half_life = max_half_life
        self.ou: OUFit | None = None
        self._n = 0

    def update(self, price: float | None) -> float | None:
        if price is None:
            return self.value
        self.prices.append(price)
        self.kalman.update(price)
        self._n += 1
        if self._n % self.refit_every == 0:
            self.ou = fit_ou(list(self.prices))
        return self.value

    @property
    def mean_reverting(self) -> bool:
        return self.ou is not None and self.ou.significant(self.t_crit, self.max_half_life)

    @property
    def value(self) -> float | None:
        lvl = self.kalman.level
        if lvl is None:
            return None
        if self.mean_reverting and self.ou_weight > 0:
            return (1 - self.ou_weight) * lvl + self.ou_weight * self.ou.expected(lvl, self.horizon)
        return lvl
