"""
Real-time event feed: news and tenders polled on a background thread.

Why: in RIT the money in news and tender cases goes to whoever reacts first.
With a single loop, a headline that lands just after a poll waits a whole loop
interval (plus the time to fetch books) before the bot sees it. The feed polls
the two event endpoints every `poll` seconds (default 100 ms) and sets an Event
that the Runner waits on, so the strategy runs IMMEDIATELY when something new
arrives instead of at the next scheduled loop.

    feed = EventFeed(client, poll=0.1, tenders=True)
    feed.start()
    feed.wait(0.25)            # returns early when news/tenders arrive
    for item in feed.drain_news(): ...
    for t in feed.tenders(): ...
"""

from __future__ import annotations

import logging
import threading
import time

from .client import RITClient, RITError

log = logging.getLogger("ritc.feed")


class EventFeed:
    def __init__(self, client: RITClient, poll: float = 0.1, news: bool = True, tenders: bool = False):
        self.client = client
        self.poll = poll
        self.want_news = news
        self.want_tenders = tenders
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._news: list[dict] = []
        self._last_news_id = 0
        self._tenders: dict[int, dict] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.latency_ms: float = 0.0          # last poll round-trip

    # ------------------------------------------------------------- lifecycle
    def start(self) -> EventFeed:
        if self._thread is None and (self.want_news or self.want_tenders):
            self._thread = threading.Thread(target=self._run, name="ritc-feed", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self.poll_once()
            except RITError as exc:
                log.debug("feed poll failed: %s", exc)
            self.latency_ms = 1000 * (time.monotonic() - t0)
            self._stop.wait(max(0.0, self.poll - (time.monotonic() - t0)))

    def poll_once(self) -> bool:
        """One poll of the event endpoints. Returns True if anything new arrived."""
        fresh = False
        if self.want_news:
            items = self.client.news(since=self._last_news_id or None)
            new = sorted((n for n in items if int(n.get("news_id", 0)) > self._last_news_id),
                         key=lambda n: int(n.get("news_id", 0)))
            if new:
                with self._lock:
                    self._news.extend(new)
                    self._last_news_id = int(new[-1]["news_id"])
                fresh = True
        if self.want_tenders:
            current = {int(t["tender_id"]): t for t in self.client.tenders() if "tender_id" in t}
            with self._lock:
                if set(current) - set(self._tenders):
                    fresh = True
                self._tenders = current
        if fresh:
            self.event.set()
        return fresh

    # ------------------------------------------------------------- consumers
    def wait(self, timeout: float) -> bool:
        """Sleep up to `timeout`; return early (True) when a new event arrives."""
        hit = self.event.wait(timeout)
        self.event.clear()
        return hit

    def drain_news(self) -> list[dict]:
        with self._lock:
            out, self._news = self._news, []
        return out

    def tenders(self) -> list[dict]:
        with self._lock:
            return list(self._tenders.values())
