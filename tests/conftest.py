import pytest

from ritc.core.book import OrderBook


def make_book(bids, asks, ticker="X"):
    """bids/asks: lists of (price, qty)."""
    return OrderBook.from_api(
        {"bid": [{"price": p, "quantity": q} for p, q in bids],
         "ask": [{"price": p, "quantity": q} for p, q in asks]},
        ticker,
    )


@pytest.fixture
def book():
    return make_book([(9.99, 1000), (9.98, 2000), (9.95, 5000)],
                     [(10.01, 1000), (10.02, 2000), (10.05, 5000)])
