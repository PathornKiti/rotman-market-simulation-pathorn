from conftest import make_book
from ritc.core.book import OrderBook
from ritc.core.client import RITClient, slice_qty
from ritc.core.risk import LimitGroup, RiskManager


def test_from_api_accepts_singular_and_plural_and_filters_own():
    payload = {"bids": [{"price": 1.0, "quantity": 10, "trader_id": "ME"},
                        {"price": 0.9, "quantity": 5, "quantity_filled": 5}],
               "ask": [{"price": 1.1, "quantity": 7}]}
    b = OrderBook.from_api(payload, "T", exclude_trader="ME")
    assert b.bids == [] and b.best_ask == 1.1


def test_walk_vwap(book):
    f = book.walk("BUY", 2000)
    assert f.complete and f.filled == 2000
    assert abs(f.vwap - 10.015) < 1e-9 and f.worst == 10.02 and f.touch == 10.01


def test_walk_incomplete(book):
    f = book.walk("SELL", 10_000)
    assert not f.complete and f.filled == 8000


def test_max_qty_within_respects_vwap(book):
    q = book.max_qty_within("BUY", 10.015, 10_000)
    assert book.walk("BUY", q).vwap <= 10.015 + 1e-12
    assert book.walk("BUY", q + 1).vwap > 10.015
    assert book.max_qty_within("SELL", 10.00, 10_000) == 0


def test_microprice_and_imbalance():
    b = make_book([(10.0, 900)], [(10.1, 100)])
    assert b.microprice() > b.mid          # heavy bid -> leans up
    assert b.imbalance() > 0


def test_round_limit_safe_direction():
    assert RITClient.round_limit(9.999, "BUY") == 9.99
    assert RITClient.round_limit(9.991, "SELL") == 10.00
    assert RITClient.round_limit(12.30, "BUY") == 12.30


def test_slice_qty():
    assert list(slice_qty(25_000, 10_000)) == [10_000, 10_000, 5_000]


def test_risk_room_net_and_gross():
    r = RiskManager(groups=[LimitGroup("eq", gross=1000, net=500, weights={"A": 1, "B": 2})], buffer=1.0)
    assert r.room("A", "BUY", {}) == 500
    assert r.room("B", "BUY", {}) == 250
    # Reducing a position is always allowed up to flat, then gross binds.
    assert r.room("A", "SELL", {"A": 400}) == 900   # 400 to flat, then 500 more short
    assert r.room("A", "BUY", {"A": 500}) == 0


def test_risk_pattern_and_cap():
    r = RiskManager(max_position={"X": 100}, groups=[LimitGroup("o", 50, 50, {}, r"^OPT")], buffer=1.0)
    assert r.room("OPT1", "BUY", {}) == 50
    assert r.room("X", "BUY", {"X": 60}) == 40
    r.halted = True
    assert r.room("X", "BUY", {}) == 0
