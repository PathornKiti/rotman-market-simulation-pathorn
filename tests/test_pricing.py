import math

from ritc.pricing.news import parse_inventory_news, parse_vol_news
from ritc.pricing.options import bs_greeks, bs_price, implied_vol, put_call_parity_gap
from ritc.pricing.stats import EWMA, RollingZ


def test_bs_known_value():
    # Hull example: S=42, K=40, r=10%, sigma=20%, T=0.5 -> call 4.76, put 0.81
    assert abs(bs_price(42, 40, 0.5, 0.1, 0.2, True) - 4.76) < 0.01
    assert abs(bs_price(42, 40, 0.5, 0.1, 0.2, False) - 0.81) < 0.01


def test_put_call_parity_holds_for_model_prices():
    c = bs_price(50, 52, 0.25, 0.03, 0.3, True)
    p = bs_price(50, 52, 0.25, 0.03, 0.3, False)
    assert abs(put_call_parity_gap(c, p, 50, 52, 0.25, 0.03)) < 1e-9


def test_implied_vol_roundtrip():
    for sigma in (0.1, 0.25, 0.6):
        for call in (True, False):
            px = bs_price(50, 48, 1 / 12, 0.0, sigma, call)
            assert abs(implied_vol(px, 50, 48, 1 / 12, 0.0, call) - sigma) < 1e-4


def test_implied_vol_rejects_arbitrage_prices():
    assert implied_vol(0.5, 50, 40, 0.1, 0.0, True) is None   # below intrinsic


def test_greeks_signs():
    g = bs_greeks(50, 50, 0.1, 0.0, 0.2, True)
    assert 0.5 < g.delta < 0.6 and g.gamma > 0 and g.vega > 0 and g.theta < 0
    gp = bs_greeks(50, 50, 0.1, 0.0, 0.2, False)
    assert abs((g.delta - gp.delta) - 1) < 1e-9


def test_parse_vol_news():
    v = parse_vol_news("The realized volatility of RTM for this week will be 22%")
    assert math.isclose(v.realized, 0.22)
    v = parse_vol_news("The annualized volatility of RTM next week will be between 18% and 24%")
    assert v.realized is None and math.isclose(v.forecast_mid, 0.21)
    v = parse_vol_news("The delta limit for this heat is 7,000 shares.")
    assert v.delta_limit == 7000


def test_parse_inventory_news():
    n = parse_inventory_news("Crude inventories show a build of 2.5 million barrels vs expected build of 1.0 million")
    assert n.actual == 2.5 and n.expected == 1.0 and n.surprise == 1.5
    n = parse_inventory_news("Weekly report: a draw of 3 million barrels. Analysts expected a draw of 1.5 million")
    assert n.actual == -3 and n.expected == -1.5
    assert parse_inventory_news("CEO resigns") is None


def test_stats():
    e = EWMA(5)
    for x in [1, 1, 1, 1]:
        e.update(x)
    assert e.mean == 1 and e.std == 0
    z = RollingZ(20)
    for i in range(20):
        z.update(float(i % 2))
    assert z.z(5.0) > 3
