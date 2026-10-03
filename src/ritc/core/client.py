"""
Thin, defensive wrapper around the RIT Client REST API v1 (default http://localhost:9999/v1).

Every method returns the raw JSON (dicts / lists) so you can print it on the
competition machine and check field names before trusting a bot with them:

    python -m ritc doctor
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from . import config


class RITError(RuntimeError):
    """Any non-2xx response from the RIT API."""


class OrdersDisabled(RITError):
    """HTTP 403: the server has API order submission switched off for this case."""


class RITClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 3.0,
        max_retries: int = 3,
        min_interval: float | None = None,
    ):
        self.base_url = (base_url or config.base_url()).rstrip("/")
        self.api_key = api_key if api_key is not None else config.api_key()
        self.timeout = timeout
        self.max_retries = max_retries
        # Client-side throttle. Raise RIT_MIN_INTERVAL if the server answers 429.
        self.min_interval = (config.env_float("RIT_MIN_INTERVAL", 0.0)
                             if min_interval is None else min_interval)
        self._last_call = 0.0
        self._lock = threading.Lock()
        # One HTTP session per thread: requests.Session is not guaranteed thread-safe,
        # and the feed + parallel fetches call the API from several threads at once.
        self._local = threading.local()
        self._pool: ThreadPoolExecutor | None = None

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            if self.api_key:
                s.headers["X-API-Key"] = self.api_key
            self._local.session = s
        return s

    def parallel(self, calls: list[Callable[[], Any]], workers: int = 8) -> list[Any]:
        """
        Run independent API calls concurrently and return results in order.
        Exceptions are returned in place of results so one failure doesn't lose the rest.
        Fetching 5 order books takes ~1 round trip instead of 5.
        """
        if len(calls) <= 1:
            out = []
            for c in calls:
                try:
                    out.append(c())
                except Exception as exc:          # noqa: BLE001 - surfaced to caller
                    out.append(exc)
            return out
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ritc-io")
        futures = [self._pool.submit(c) for c in calls]
        out = []
        for f in futures:
            try:
                out.append(f.result())
            except Exception as exc:              # noqa: BLE001
                out.append(exc)
        return out

    # ---------------------------------------------------------------- plumbing
    def _request(self, method: str, path: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            with self._lock:
                gap = time.monotonic() - self._last_call
                if self.min_interval and gap < self.min_interval:
                    time.sleep(self.min_interval - gap)
                self._last_call = time.monotonic()
            try:
                r = self.session.request(method, url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(0.2 * (attempt + 1))
                continue
            if r.status_code == 429:
                wait = 0.5
                try:
                    wait = float(r.json().get("wait", wait))
                except Exception:
                    pass
                time.sleep(min(wait, 2.0))
                last_exc = RITError(f"rate limited ({wait:.2f}s)")
                continue
            if r.status_code >= 400:
                body = r.text[:300]
                if r.status_code == 403 and "order" in body.lower():
                    raise OrdersDisabled(body)
                raise RITError(f"{method} {path} -> {r.status_code}: {body}")
            return r.json() if r.content else None
        raise RITError(f"{method} {path} failed after {self.max_retries} attempts: {last_exc}")

    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, **params)

    def post(self, path: str, **params: Any) -> Any:
        return self._request("POST", path, **params)

    def delete(self, path: str, **params: Any) -> Any:
        return self._request("DELETE", path, **params)

    # ------------------------------------------------------------------ state
    def case(self) -> dict:
        """{name, period, tick, ticks_per_period, total_periods, status}."""
        return self.get("/case")

    def trader(self) -> dict:
        """{trader_id, first_name, last_name, nlv}."""
        return self.get("/trader")

    def limits(self) -> list[dict]:
        """[{name, gross, net, gross_limit, net_limit, gross_fine, net_fine}]."""
        return self.get("/limits") or []

    def news(self, since: int | None = None, limit: int = 50) -> list[dict]:
        """[{news_id, period, tick, ticker, headline, body}] newest first."""
        return self.get("/news", since=since, limit=limit) or []

    def securities(self, ticker: str | None = None) -> list[dict]:
        return self.get("/securities", ticker=ticker) or []

    def book(self, ticker: str, limit: int = 20) -> dict:
        return self.get("/securities/book", ticker=ticker, limit=limit) or {}

    def history(self, ticker: str, limit: int | None = None) -> list[dict]:
        return self.get("/securities/history", ticker=ticker, limit=limit) or []

    def tas(self, ticker: str, after: int | None = None) -> list[dict]:
        return self.get("/securities/tas", ticker=ticker, after=after) or []

    # ----------------------------------------------------------------- orders
    def orders(self, status: str = "OPEN") -> list[dict]:
        return self.get("/orders", status=status) or []

    @staticmethod
    def round_limit(price: float, action: str, decimals: int = 2) -> float:
        """Round in the SAFE direction: floor a buy limit, ceil a sell limit."""
        scale = 10 ** decimals
        x = price * scale
        # A tiny epsilon stops 12.30 * 100 = 1229.9999 flooring to 12.29.
        v = math.floor(x + 1e-9) if action.upper() == "BUY" else math.ceil(x - 1e-9)
        return v / scale

    def market_order(self, ticker: str, action: str, quantity: int) -> dict:
        return self.post("/orders", ticker=ticker, type="MARKET",
                         quantity=int(quantity), action=action.upper())

    def limit_order(self, ticker: str, action: str, quantity: int, price: float,
                    decimals: int = 2) -> dict:
        action = action.upper()
        return self.post("/orders", ticker=ticker, type="LIMIT", quantity=int(quantity),
                         action=action, price=self.round_limit(price, action, decimals))

    def cancel(self, order_id: int) -> Any:
        return self.delete(f"/orders/{order_id}")

    def cancel_all(self, ticker: str | None = None) -> Any:
        if ticker:
            return self.post("/commands/cancel", ticker=ticker)
        return self.post("/commands/cancel", all=1)

    # ---------------------------------------------------------------- tenders
    def tenders(self) -> list[dict]:
        """[{tender_id, ticker, action, quantity, price, is_fixed_bid, expires, caption}]."""
        return self.get("/tenders") or []

    def accept_tender(self, tender_id: int, price: float | None = None) -> Any:
        return self.post(f"/tenders/{tender_id}", price=price)

    def decline_tender(self, tender_id: int) -> Any:
        return self.delete(f"/tenders/{tender_id}")

    # ----------------------------------------------------------------- leases
    # Physical assets in commodity cases (storage, refineries, pipelines).
    def leases(self) -> list[dict]:
        return self.get("/leases") or []

    def lease(self, ticker: str) -> Any:
        return self.post("/leases", ticker=ticker)

    def use_lease(self, lease_id: int, **kwargs: Any) -> Any:
        """e.g. use_lease(7, from1="CL", quantity1=10) - see the case brief for keys."""
        return self.post(f"/leases/{lease_id}", **kwargs)

    def release_lease(self, lease_id: int) -> Any:
        return self.delete(f"/leases/{lease_id}")

    # -------------------------------------------------------------- shortcuts
    def security_map(self) -> dict[str, dict]:
        return {s["ticker"]: s for s in self.securities()}

    def positions(self) -> dict[str, int]:
        return {s["ticker"]: int(s.get("position", 0)) for s in self.securities()}


def slice_qty(quantity: int, max_order_size: int) -> Iterator[int]:
    """RIT rejects oversized orders outright, so always slice."""
    quantity = int(abs(quantity))
    if max_order_size <= 0:
        raise ValueError("max_order_size must be positive")
    while quantity > 0:
        chunk = min(quantity, max_order_size)
        yield chunk
        quantity -= chunk
