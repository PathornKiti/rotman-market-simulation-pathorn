import os

import pytest

from ritc.core.book import OrderBook

# Tests never see the developer's .env (live server, credentials, dry-run switch, rate limit). Real environment
# variables beat .env, so pin them here; tuner worker processes inherit them too.
os.environ.update({"RIT_URL": "", "RIT_HOST": "localhost", "RIT_PORT": "9999", "RIT_API_KEY": "", "RIT_AUTH": "",
                   "RIT_DRY_RUN": "true", "RIT_MIN_INTERVAL": "0", "RIT_INTERVAL": "0.25"})


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
