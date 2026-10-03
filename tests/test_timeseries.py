import math
import random

import pytest

from conftest import make_book
from ritc.pricing.timeseries import (
    FairValueModel,
    Garch11,
    KalmanLevel,
    OnlineGarch,
    fit_ou,
    log_returns,
    nelder_mead,
)
from ritc.strategies.derivatives import blend_forecast
from ritc.strategies.equity import QuoteParams, compute_quotes
from ritc.strategies.liability import evaluate_tender


def simulate_garch(n, omega, alpha, beta, seed=1):
    rnd = random.Random(seed)
    s2, out = omega / (1 - alpha - beta), []
    for _ in range(n):
        e = rnd.gauss(0, math.sqrt(s2))
        out.append(e)
        s2 = omega + alpha * e * e + beta * s2
    return out


def test_nelder_mead_quadratic():
    x = nelder_mead(lambda v: (v[0] - 3) ** 2 + (v[1] + 1) ** 2, [0.0, 0.0])
    assert abs(x[0] - 3) < 1e-3 and abs(x[1] + 1) < 1e-3


def test_garch_recovers_parameters():
    r = simulate_garch(3000, 2e-6, 0.10, 0.85, seed=3)
    m = Garch11.fit(r)
    assert abs(m.alpha - 0.10) < 0.04 and abs(m.beta - 0.85) < 0.06
    assert m.persistence < 1


def test_garch_forecast_reverts_to_long_run():
    m = Garch11(omega=1e-6, alpha=0.1, beta=0.85, sigma2=1e-4)       # far above long-run 2e-5
    f = m.forecast(200)
    assert f[0] == pytest.approx(1e-4)
    assert f[-1] == pytest.approx(m.long_run_var, rel=1e-3)
    assert all(a >= b for a, b in zip(f, f[1:]))                     # decays monotonically
    # term vol over a short horizon is higher than over a long one when vol is elevated
    assert m.term_vol(5) > m.term_vol(200) > math.sqrt(m.long_run_var)
    # closed form matches the explicit average
    assert m.term_vol(50) == pytest.approx(math.sqrt(sum(m.forecast(50)) / 50), rel=1e-9)


def test_garch_needs_data():
    with pytest.raises(ValueError):
        Garch11.fit([0.01] * 5)


def test_online_garch_switches_from_ewma_to_garch():
    g = OnlineGarch(min_obs=60, refit_every=50)
    for r in simulate_garch(59, 2e-6, 0.1, 0.85):
        g.add_return(r)
    assert g.model is None and g.ready and g.vol() > 0
    g.add_return(0.001)
    assert g.model is not None
    assert g.annualised(100, 3600) == pytest.approx(g.term_vol(100) * 60)


def test_log_returns():
    assert log_returns([100, 110, 99]) == pytest.approx([math.log(1.1), math.log(0.9)])


def test_ou_detects_mean_reversion_and_rejects_random_walk():
    rnd = random.Random(5)
    x, mr = 50.0, []
    for _ in range(600):
        x += 0.08 * (52 - x) + rnd.gauss(0, 0.1)
        mr.append(x)
    fit = fit_ou(mr)
    assert fit.significant() and abs(fit.mu - 52) < 0.3 and 4 < fit.half_life < 20
    assert fit.expected(50.0, 1e6) == pytest.approx(fit.mu)
    rw = [50.0]
    for _ in range(600):
        rw.append(rw[-1] + rnd.gauss(0, 0.1))
    assert not fit_ou(rw).significant()
    assert fit_ou([1.0] * 5) is None


def test_kalman_denoises():
    rnd = random.Random(2)
    k, true, ek, ep = KalmanLevel(), 50.0, 0.0, 0.0
    for i in range(3000):
        true += rnd.gauss(0, 0.01)
        obs = true + rnd.gauss(0, 0.05)
        lvl = k.update(obs)
        if i > 300:
            ek += (lvl - true) ** 2
            ep += (obs - true) ** 2
    assert ek < 0.6 * ep


def test_fair_value_model_only_leans_when_mean_reverting():
    rnd = random.Random(4)
    fv = FairValueModel(refit_every=25, horizon=10, ou_weight=1.0)
    x = 50.0
    for _ in range(300):
        x += 0.1 * (55 - x) + rnd.gauss(0, 0.05)
        fv.update(x)
    assert fv.mean_reverting
    fv.update(53.0)                                     # shock below the mean
    assert fv.value > fv.kalman.level                   # expects a move back up


def test_blend_forecast():
    assert blend_forecast(0.30, 0.20, 0.25, 0.1) == pytest.approx(0.275)
    assert blend_forecast(None, 0.20, 0.25, 0.1) == 0.20
    assert blend_forecast(0.30, None, 0.25, 0.1) == 0.30
    assert blend_forecast(None, None, 0.25, 0.1) == 0.1


def test_equity_fair_shift_leans_quotes_but_is_capped():
    book = make_book([(9.90, 1000)], [(10.10, 1000)])
    p = QuoteParams(min_half_spread=0.02, vol_mult=1.0, skew_per_share=0.0, imbalance_lean=0.0)
    base = compute_quotes(book, 0, 0.0, p)
    up = compute_quotes(book, 0, 0.0, p, fair_shift=0.01)
    huge = compute_quotes(book, 0, 0.0, p, fair_shift=5.0)
    assert up.bid > base.bid and up.ask > base.ask
    assert huge.fair - base.fair == pytest.approx(0.02)       # capped at one half-spread


def test_liability_volatility_raises_required_edge():
    book = make_book([(25.00, 20000), (24.98, 20000)], [(25.02, 20000), (25.04, 20000)])
    t = {"action": "BUY", "quantity": 20000, "price": 24.92, "is_fixed_bid": True}
    calm = evaluate_tender(t, book, 0.02, unwind_ticks_per_lot=0.001, price_vol_per_tick=0.01, risk_aversion=0.5)
    wild = evaluate_tender(t, book, 0.02, unwind_ticks_per_lot=0.001, price_vol_per_tick=0.05, risk_aversion=0.5)
    assert calm.accept and not wild.accept


def test_volatility_clustering_test():
    from ritc.pricing.timeseries import volatility_clusters
    clustered = simulate_garch(1500, 2e-6, 0.15, 0.80, seed=9)
    rnd = random.Random(9)
    iid = [rnd.gauss(0, 0.01) for _ in range(1500)]
    assert volatility_clusters(clustered)[0]
    assert not volatility_clusters(iid)[0]
