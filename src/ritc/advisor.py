"""
MANUAL-TRADING ADVISOR (liability case): connect to the live RIT API READ-ONLY and show, every
loop, what the bot would do, so you can type it into the RIT client yourself.

    python -m ritc suggest                 # same RIT_URL / RIT_API_KEY as `run`
    python -m ritc suggest --set strategy.min_profit_per_share=0.03

It runs the real LiabilityStrategy in dry-run against YOUR account, so your manual fills move the
positions it plans from, and every non-GET call (orders, cancels, tender answers) is refused at
the client. On screen:

    TENDERS   ACCEPT / DECLINE / BID <price>, the expected $ and WHEN to answer (the bot answers
              `decide_late_ticks` before expiry with the book of then; until then: WAIT)
    ORDERS    per stock, the limit orders the bot would have working: REST = post at the touch
              (earns the spread), CROSS = marketable limit (cancel what is left a second later)
    WARNINGS  your resting orders that would be fined: past zero (speculation), on the wrong
              side, or on a stock whose tender is still open (front-running)

Decisions are logged to logs/suggest-liability-<time>.log (not picked up by `ritc report`).
"""

from __future__ import annotations

import logging
import os
import time

from .core.bot import Snapshot
from .core.client import RITError
from .core.logging_setup import setup as setup_logging

log = logging.getLogger("ritc.suggest")

RED, GREEN, YELLOW, BOLD, RESET = "\033[31m", "\033[32m", "\033[33m", "\033[1m", "\033[0m"


def read_only(client) -> None:
    """Refuse every call that could trade: only GET reaches the server."""
    real = client._request

    def guarded(method, path, **params):
        if method != "GET":
            raise RITError(f"suggest mode is read-only: refused {method} {path}")
        return real(method, path, **params)
    client._request = guarded


def connection_problem(client) -> str | None:
    """None when the RIT API answers; otherwise what to fix, in words."""
    try:
        client.trader()
        client.case()
    except RITError as exc:
        msg = str(exc)
        if not client.api_key and not client.auth:      # the offline simulator needs neither, RIT does
            return ("no credentials: RIT_API_KEY (RIT client, API icon) or RIT_AUTH (DMA) in .env")
        if "401" in msg:
            return "API key rejected (401): copy it again from the RIT client's API icon into .env"
        if "failed after" in msg and "localhost" in client.base_url:
            return (f"no RIT API at {client.base_url}: is the RIT client open and logged in on THIS machine? "
                    "(RIT_HOST / RIT_PORT in .env if not)")
        if "failed after" in msg:
            return (f"no answer from {client.base_url}: server down / heat not open, or this network can't "
                    "reach it (campus network or VPN?)")
        return msg[:200]
    return None


ENV_KEYS = ("RIT_API_KEY", "RIT_AUTH", "RIT_URL", "RIT_HOST", "RIT_PORT")


def reload_env(client, real: set[str]) -> None:
    """Pick up a key pasted into .env while we wait. `real`: keys set in the real environment, which still win."""
    import threading

    from .core import config
    for k, v in config.load_env().items():
        if k in ENV_KEYS and k not in real:
            os.environ[k] = v
    new = (config.api_key(), config.auth(), config.base_url())
    if (client.api_key, client.auth, client.base_url) != new:
        client.api_key, client.auth, client.base_url = new
        client._local = threading.local()            # new sessions carry the new X-API-Key header


class LastWarning(logging.Handler):
    """Keeps the latest warning (API errors, slow loops) for the screen; the log file has them all."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.text = ""

    def emit(self, record: logging.LogRecord) -> None:
        self.text = f"{time.strftime('%H:%M:%S')} {record.getMessage()[:160]}"


def _left(o: dict) -> int:
    return int(o.get("quantity", 0) or 0) - int(o.get("quantity_filled", 0) or 0)


def order_warnings(ticker: str, pos: int, orders: list[dict], frozen: bool) -> list[str]:
    """What is wrong with YOUR resting orders on `ticker` (2027 brief: every share that opens a
    position is fined as speculation, every fill while one of its tenders is open as front-running)."""
    mine = [o for o in orders if o.get("ticker") == ticker and _left(o) > 0]
    if not mine:
        return []
    if frozen:
        return [f"CANCEL all {ticker} orders: a fill while its tender is open is front-running"]
    if pos == 0:
        return [f"CANCEL all {ticker} orders: you are flat, any fill opens a position (speculation)"]
    unwind = "SELL" if pos > 0 else "BUY"
    out = []
    wrong = sum(_left(o) for o in mine if str(o.get("action", "")).upper() != unwind)
    if wrong:
        out.append(f"CANCEL {wrong:,} {ticker} {'BUY' if unwind == 'SELL' else 'SELL'}: it adds to the position")
    same = sum(_left(o) for o in mine if str(o.get("action", "")).upper() == unwind)
    if same > abs(pos):
        out.append(f"CANCEL {same - abs(pos):,} of your {unwind} {ticker}: {same:,} resting > position "
                   f"{abs(pos):,} goes through zero (speculation)")
    return out


class Advisor:
    def __init__(self, strategy, color: bool = True, warning: LastWarning | None = None):
        self.s = strategy
        self.snap: Snapshot | None = None
        self.color = color
        self.rung: set[int] = set()
        self.warning = warning

    def c(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.color else text

    def tender_lines(self, snap: Snapshot, tenders: list[dict]) -> list[str]:
        s, out = self.s, []
        late = int(s.p.get("decide_late_ticks", 0))
        for t in tenders:
            tid, ticker = int(t.get("tender_id", -1)), t.get("ticker")
            if ticker is None:
                continue
            action, qty, px = str(t.get("action", "BUY")).upper(), int(t.get("quantity", 0)), t.get("price")
            fixed = bool(t.get("is_fixed_bid", True))
            d = s.decide(t, snap, snap.positions)
            if not fixed:
                verdict = self.c(GREEN, f"BID {d.price:.2f}") if d.accept else self.c(RED, "DON'T BID")
            else:
                verdict = self.c(GREEN, "ACCEPT") if d.accept else self.c(RED, "DECLINE")
            try:
                left = int(t["expires"]) - snap.tick
                when = (self.c(YELLOW, f"WAIT, answer at t={int(t['expires']) - late}")
                        if s.wait_to_decide(t, tid, snap) else self.c(BOLD, "ANSWER NOW"))
                exp = f"expires t={t['expires']} ({left} s)"
            except (KeyError, TypeError, ValueError):
                when, exp = self.c(BOLD, "ANSWER NOW"), "expiry unknown"
            kind = "fixed" if fixed else "AUCTION"
            price = f"@ {float(px):.2f}" if px is not None and fixed else (f"ref {float(px):.2f}" if px else "")
            out.append(f"#{tid} you {action} {ticker} {qty:,} {price} ({kind})  {exp}")
            out.append(f"    -> {verdict}  {d.profit_per_share:+.3f}/sh = ${d.profit_per_share * qty:+,.0f}"
                       f"  exit ~{d.unwind_vwap:.2f}   {when}")
            out.append(f"       {d.reason}")
        return out

    def order_lines(self, snap: Snapshot, frozen: dict[str, int], orders: list[dict]) -> list[str]:
        s, algo, now, out = self.s, self.s.algo, snap.abs_tick, []
        hold = algo.passive_from is not None and now >= algo.passive_from
        for t in s.tickers:
            pos, book = snap.positions.get(t, 0), snap.book(t)
            touch = f"bid {book.best_bid or 0:.2f} / ask {book.best_ask or 0:.2f}"
            out.append(f"{self.c(BOLD, t)}  pos {pos:+,}   {touch}")
            if t in frozen:
                out.append(self.c(YELLOW, f"    HOLD: tender #{frozen[t]} still open, don't trade {t}"))
            elif pos:
                b = algo.blocks.get(t)
                kids = algo._kids(t, b, book, pos, now) if b else []
                if not kids:
                    out.append("    no order: hold (a booked-later tender offsets it, or no market)")
                elif b:
                    mode = (" HOLD-TO-BELL: rest only, the rest closes at the last price"
                            if hold else " within risk budget: rest only" if t in algo.passive else "")
                    out.append(f"    unwind {abs(b.target - pos):,} to {b.target:+,} by t="
                               f"{b.deadline - (snap.period - 1) * snap.ticks_per_period}"
                               + self.c(YELLOW, mode))
                for k in kids:
                    how = ("REST at the touch" if k.passive
                           else "CROSS now (marketable), cancel the leftover in ~1 s")
                    out.append(self.c(GREEN, f"    LIMIT {k.action} {k.qty:,} @ {k.price:.2f}") + f"   {how}")
            for o in orders:
                if o.get("ticker") == t and _left(o) > 0:
                    px = float(o.get("price") or 0)
                    out.append(f"    yours: {str(o.get('action')).upper()} {_left(o):,} @ {px:.2f}")
            out += [self.c(RED, "    ! " + w) for w in order_warnings(t, pos, orders, t in frozen)]
        return out

    def render(self) -> None:
        snap, s = self.snap, self.s
        if snap is None:
            return
        try:
            orders = s.client.orders("OPEN")
        except RITError:
            orders = []
        tenders = s.current_tenders()
        if any(int(t.get("tender_id", -1)) not in self.rung for t in tenders):
            print("\a", end="")                                   # new tender: ring the terminal bell
            self.rung |= {int(t.get("tender_id", -1)) for t in tenders}
        frozen = {t["ticker"]: int(t.get("tender_id", -1)) for t in tenders if t.get("ticker")}
        lim = ""
        for g in s.risk.groups:
            gross, net = g.exposure(snap.positions)
            lim += f"  {g.name}: gross {gross:,.0f}/{g.gross:,.0f} net {net:+,.0f}/{g.net:,.0f}"
        crowd = (f"crowd {'DETECTED' if s.crowded() else 'not detected'} ({s.crowd.mean:+.3f} $/10k, n={s.crowd.n})"
                 if s.crowd_learn else "")
        lines = [f"{snap.case.get('name', '')}  tick {snap.tick}/{snap.ticks_per_period} ({snap.ticks_left} s left)"
                 f"   NLV {snap.nlv or 0:,.0f}" + lim,
                 f"SUGGEST MODE - read-only, nothing is sent. {crowd}", "",
                 self.c(BOLD, "TENDERS")] + (self.tender_lines(snap, tenders) or ["  none"])
        lines += ["", self.c(BOLD, "ORDERS (type these in the RIT client)")] + self.order_lines(snap, frozen, orders)
        if self.warning and self.warning.text:
            lines += ["", self.c(RED, "last warning: " + self.warning.text)]
        print("\033[2J\033[H" + "\n".join(lines), flush=True)


def suggest(cfg_path: str | None = None, overrides: dict | None = None, interval: float = 0.5,
            color: bool = True) -> int:
    from .cli import build
    real = {k for k in ENV_KEYS if k in os.environ}      # before .env is loaded: these beat .env
    if os.name == "nt":
        os.system("")                              # turn on ANSI colours / screen clearing in the Windows console
    path = setup_logging("suggest-liability")
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]   # the screen is the dashboard
    warning = LastWarning()
    root.addHandler(warning)
    runner, _ = build("liability", cfg_path, live=False, overrides=overrides)
    runner.s.ex.dry_run = True                     # belt and braces: RIT_DRY_RUN=0 must not matter here
    read_only(runner.client)
    runner.interval = interval
    shown = None
    try:
        while (problem := connection_problem(runner.client)) is not None:
            if problem != shown:
                print(f"not connected: {problem}  (retrying every 2 s, Ctrl-C to stop)", flush=True)
                shown = problem
            time.sleep(2)
            reload_env(runner.client, real)
    except KeyboardInterrupt:
        return 1
    tr, case = runner.client.trader(), runner.client.case()
    print(f"connected to {runner.client.base_url} as {tr.get('trader_id')}: case {case.get('name')} "
          f"is {case.get('status')}", flush=True)
    adv = Advisor(runner.s, color, warning)
    take = runner.snapshot

    def keep():
        adv.snap = take()
        return adv.snap
    runner.snapshot = keep
    runner.on_loop = adv.render
    try:
        runner.s.risk.update_from_api(runner.client.limits())
    except RITError:
        pass
    print(f"suggest mode (read-only), log {path}. Waiting for the case to be ACTIVE... Ctrl-C to stop.", flush=True)
    t0 = time.monotonic()
    runner.run()
    print(f"stopped after {time.monotonic() - t0:.0f} s")
    return 0
