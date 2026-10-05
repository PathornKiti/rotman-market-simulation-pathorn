"""Research-grade components: Almgren-Chriss, Bertram, Bayesian impact."""

import math
import random

from conftest import make_book
from ritc.core.algo import ac_kappa, book_eta, schedule_position
from ritc.pricing.timeseries import bertram_band, ou_continuous, ou_passage_time
from ritc.strategies.commodity import BayesImpact


# ------------------------------------------------------------ Almgren-Chriss
def test_ac_kappa_scales_with_risk_and_impact():
    assert ac_kappa(0.0, 1e-6, 1e-5) == 0.0                     # missing input -> fallback
    k = ac_kappa(0.025, 1.25e-6, 1e-5)
    assert abs(k - math.sqrt(1e-5 * 0.025 ** 2 / 1.25e-6)) < 1e-12
    assert ac_kappa(0.05, 1.25e-6, 1e-5) > k                    # more vol -> more urgency
    assert ac_kappa(0.025, 5e-6, 1e-5) < k                      # more impact -> less urgency


def test_ac_schedule_endpoints_and_front_loading():
    pts = [schedule_position(10000, 0, 0, 30, t, kappa=0.1) for t in (0, 10, 30)]
    assert pts[0] == 10000 and pts[-1] == 0
    linear = schedule_position(10000, 0, 0, 30, 10)
    assert pts[1] < linear                                      # AC front-loads vs linear
    near_linear = schedule_position(10000, 0, 0, 30, 10, kappa=1e-4)
    assert abs(near_linear - linear) <= 2                       # kappa -> 0 is the straight line


def test_book_eta_from_depth():
    book = make_book([(25.00, 20000), (24.95, 20000)], [(25.02, 20000)])
    # 40,000 shares within $0.10 of the bid -> rho = 400,000 / $ -> eta = 1 / (2 rho)
    assert abs(book_eta(book, "bid") - 1 / 800_000) < 1e-12
    assert book_eta(make_book([], [(25.0, 100)]), "bid") == 0.0


# ------------------------------------------------------------ Bertram
def test_ou_passage_time_matches_monte_carlo():
    theta, sigma, a = 0.08, 0.06, 0.15
    rng, times = random.Random(3), []
    for _ in range(1500):
        x, t = -a, 0
        while x < a:
            x += -theta * x * 0.1 + sigma * math.sqrt(0.1) * rng.gauss(0, 1)
            t += 0.1
        times.append(t)
    mc = sum(times) / len(times)
    assert abs(ou_passage_time(theta, sigma, a) / mc - 1) < 0.1


def test_bertram_band_widens_with_cost_and_beats_neighbours():
    th, sg = ou_continuous(type("F", (), {"b": 0.92, "sigma": 0.06})())
    a1, mu1 = bertram_band(th, sg, 0.10)
    a2, _ = bertram_band(th, sg, 0.40)
    assert a2 > a1 > 0.05
    for a in (a1 * 0.7, a1 * 1.3):                              # a* is a maximum
        assert (2 * a - 0.10) / ou_passage_time(th, sg, a) <= mu1 + 1e-12
    assert bertram_band(0.0, 0.06, 0.1) == (0.0, 0.0)


# ------------------------------------------------------------ Bayesian impact
def test_bayes_impact_recovers_truth_from_wrong_prior():
    b, rng = BayesImpact(mean=0.10, sd=0.15, noise_sd=0.10), random.Random(1)
    for _ in range(8):
        x = rng.uniform(-3, 3)
        b.update(x, 0.25 * x + rng.gauss(0, 0.10))
    assert abs(b.mean - 0.25) < 0.04 and b.sd < 0.03 and b.n == 8
    b.update(0.0, 1.0)                                          # no surprise -> no information
    assert b.n == 8
