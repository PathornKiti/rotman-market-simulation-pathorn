"""
Parse a `liability` bot log (logs/liability-<date>-<time>.log) into a structured run.

Handles every format the bot has written so far:

* v1 "instant": tenders answered the moment they appear (`TENDER id ... -> ACCEPT (...)` only);
* v2 "late": `... seen at tick X, expires E: answering at tick Y` before each answer;
* v3 "late + end guard": `... answering 3 ticks before it` and `VOL` lines every 60 s.

Several bots writing to ONE file (parallel dry runs started in the same second) are kept apart by
keying tenders on (id, side, ticker, size) instead of the id alone; `bots` counts them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

LINE = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d\d\d) (\w+)\s+([\w.]+) \| (.*)$")
SEEN = re.compile(r"TENDER (\d+) (BUY|SELL) (\w+) x(\d+) @ (\S+) seen at tick (\d+), expires (\S+): "
                  r"answering (?:at tick (\d+)|(\d+) ticks before it)")
DECIDE = re.compile(r"TENDER (\d+) (BUY|SELL) (\w+) x(\d+) @ (\S+) -> (ACCEPT|decline) \((.*)\)$")
REJECT = re.compile(r"TENDER (\d+) not filled \(competitive bid (\S+) rejected\)")
PPS = re.compile(r"profit/share ([+-]\d+\.\d+)")
BID = re.compile(r"competitive bid (\d+\.\d+)")
ORDER = re.compile(r"\[(LIVE|DRY)\] (BUY|SELL) (\w+) (\d+) @ (\d+\.\d+)")
BLOCK = re.compile(r"BLOCK (start|done) (\w+) ([+-]\d+) -> ([+-]\d+) by tick (\d+)(?: limit (\S+))?"
                   r"(?: AC kappa\*T (\S+))?")
CROWD = re.compile(r"CROWD (\w+) moved ([+-]\d+\.\d+) against a (\d+)k-share unwind -> ([+-]?\d+\.\d+) "
                   r"\$/10k \(sd (\d+\.\d+), n=(\d+)\)")
VOL = re.compile(r"(\w+) \$(\d+\.\d+)/tick")
TCA = re.compile(r"^(\w+)\s+(passive|aggressive)\s+(\d+)\s+(\d+)%\s+([+-]?\d+\.\d+)\s+([+-]?[\d,]+\.\d+)")
PORT = re.compile(r"port=(\d+)")
LOOP = re.compile(r"loop p50 (\d+) ms, p95 (\d+) ms, max (\d+) ms")
STAMP = re.compile(r"(\d{8})-(\d{6})")


@dataclass
class Decision:
    wall: float
    accept: bool
    pps: float | None             # the bot's estimated profit/share after costs
    bid: float | None             # auction / winner-take-all price submitted
    reason: str
    price: float | None           # the tender price this bot saw (differs across market variants)


@dataclass
class Tender:
    tid: int
    action: str                   # OUR side: BUY = we buy the block and must sell it
    ticker: str
    qty: int
    fixed: bool                   # False = competitive auction or winner-take-all (no price shown)
    seen_wall: float | None = None
    seen_tick: int | None = None
    expires: int | None = None
    decisions: list[Decision] = field(default_factory=list)
    rejected: list[tuple[float, float]] = field(default_factory=list)   # (wall, bid) answered success=false
    prices: list[float] = field(default_factory=list)                  # every fixed price logged

    @property
    def key(self) -> tuple:
        return (self.tid, self.action, self.ticker, self.qty)

    @property
    def accepted(self) -> bool:
        return any(d.accept for d in self.decisions)


@dataclass
class Run:
    path: Path
    stamp: str = ""               # 20261010-105313 (from the file name)
    lines: int = 0
    bots: int = 0
    dry_run: bool | None = None
    version: str = "v1-instant"
    t0: float | None = None
    t1: float | None = None
    tenders: dict[tuple, Tender] = field(default_factory=dict)
    orders: list[tuple] = field(default_factory=list)        # (wall, mode, side, ticker, qty, px)
    blocks: list[tuple] = field(default_factory=list)        # (wall, kind, ticker, start, target, deadline, kT)
    crowd: list[tuple] = field(default_factory=list)         # (wall, ticker, move, size k, mean, sd, n)
    vol: list[tuple] = field(default_factory=list)           # (wall, {ticker: $/tick})
    api_errors: list[tuple] = field(default_factory=list)
    feed_errors: int = 0
    slow_loops: list[tuple] = field(default_factory=list)    # (wall, ms)
    cancel_fail: list[tuple] = field(default_factory=list)
    tender_fail: list[tuple] = field(default_factory=list)
    crashed: bool = False
    shutdown: bool = False
    loop_stats: list[tuple] = field(default_factory=list)    # (p50, p95, max) ms
    tca: list[dict] = field(default_factory=list)
    ports: set = field(default_factory=set)
    raw: list[str] = field(default_factory=list)
    anchors: list[tuple] = field(default_factory=list)       # extra (wall, tick) points, e.g. from a seed match

    @property
    def duration(self) -> float:
        return (self.t1 - self.t0) if self.t0 is not None and self.t1 is not None else 0.0

    def tender_list(self) -> list[Tender]:
        return [self.tenders[k] for k in sorted(self.tenders)]

    def clock(self) -> tuple[float, float] | None:
        """(tick at t0, ticks per second) fitted from `seen at tick` lines and block deadlines."""
        pts = [(t.seen_wall, t.seen_tick) for t in self.tenders.values() if t.seen_tick is not None]
        if len(pts) < 3:
            pts += list(self.anchors)
        if len(pts) < 3:
            pts += [(w, dl - 30) for w, kind, _, _, _, dl, _ in self.blocks if kind == "start" and dl < 400]
        if len(pts) < 2:
            return None
        pts.sort()
        n = len(pts)
        mx = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        sxx = sum((p[0] - mx) ** 2 for p in pts)
        if sxx <= 0:
            return None
        b = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
        return my - b * (mx - self.t0), b

    def tick_at(self, wall: float) -> int | None:
        c = self.clock()
        if c is None:
            return None
        return int(round(c[0] + c[1] * (wall - self.t0)))


def _wall(m) -> float:
    h, mi, s, ms = (int(m.group(i)) for i in range(1, 5))
    return h * 3600 + mi * 60 + s + ms / 1000


def _px(raw: str) -> float | None:
    return None if raw == "None" else float(raw)


def _tender(run: Run, tid: int, action: str, ticker: str, qty: int, price: float | None) -> Tender:
    key = (tid, action, ticker, qty)
    t = run.tenders.get(key)
    if t is None:
        t = run.tenders[key] = Tender(tid, action, ticker, qty, price is not None)
    if price is not None and price not in t.prices:
        t.prices.append(price)
    return t


def parse_lines(lines: list[str], path: str | Path = "<memory>") -> Run:
    run = Run(Path(path))
    m = STAMP.search(run.path.name)
    run.stamp = f"{m.group(1)}-{m.group(2)}" if m else run.path.stem
    last_wall, day = None, 0.0
    by_tid: dict[int, list[Tender]] = {}
    for raw in lines:
        run.lines += 1
        run.raw.append(raw)
        m = LINE.match(raw)
        if not m:
            t = TCA.match(raw.strip())
            if t:
                run.tca.append({"ticker": t.group(1), "style": t.group(2), "filled": int(t.group(3)),
                                "fill_pct": int(t.group(4)), "per_unit": float(t.group(5)),
                                "total": float(t.group(6).replace(",", ""))})
            continue
        w = _wall(m) + day
        if last_wall is not None and w < last_wall - 3600:       # ran past midnight
            day += 86400
            w += 86400
        last_wall = w
        run.t0 = w if run.t0 is None else run.t0
        run.t1 = w
        logger, msg = m.group(6), m.group(7)
        for p in PORT.findall(msg):
            run.ports.add(int(p))
        if logger == "ritc.bot":
            if msg.startswith("strategy="):
                run.bots += 1
                run.dry_run = "dry_run=True" in msg
            elif msg.startswith("API error"):
                run.api_errors.append((w, msg))
            elif msg.startswith("slow loop"):
                run.slow_loops.append((w, int(re.search(r"(\d+) ms", msg).group(1))))
            elif msg.startswith("strategy crashed"):
                run.crashed = True
            elif msg.startswith("shutting down"):
                run.shutdown = True
            lp = LOOP.search(msg)
            if lp:
                run.loop_stats.append(tuple(int(x) for x in lp.groups()))
        elif logger == "ritc.feed":
            run.feed_errors += 1
        elif logger == "ritc.exec":
            o = ORDER.search(msg)
            if o:
                run.orders.append((w, o.group(1), o.group(2), o.group(3), int(o.group(4)), float(o.group(5))))
            elif "cancel" in msg:
                run.cancel_fail.append((w, msg))
        elif logger == "ritc.algo":
            b = BLOCK.search(msg)
            if b:
                run.blocks.append((w, b.group(1), b.group(2), int(b.group(3)), int(b.group(4)), int(b.group(5)),
                                   float(b.group(7)) if b.group(7) else None))
        elif logger == "ritc.liability":
            if msg.startswith("CROWD"):
                c = CROWD.search(msg)
                if c:
                    run.crowd.append((w, c.group(1), float(c.group(2)), int(c.group(3)), float(c.group(4)),
                                      float(c.group(5)), int(c.group(6))))
                continue
            if msg.startswith("VOL"):
                run.vol.append((w, {k: float(v) for k, v in VOL.findall(msg)}))
                run.version = "v3-late-guard"
                continue
            sm = SEEN.search(msg)
            if sm:
                t = _tender(run, int(sm.group(1)), sm.group(2), sm.group(3), int(sm.group(4)), _px(sm.group(5)))
                by_tid.setdefault(t.tid, []).append(t)
                if t.seen_wall is None:
                    t.seen_wall, t.seen_tick = w, int(sm.group(6))
                    t.expires = int(sm.group(7)) if sm.group(7).isdigit() else None
                run.version = "v2-late" if sm.group(8) else "v3-late-guard"
                continue
            d = DECIDE.search(msg)
            if d:
                price = _px(d.group(5))
                t = _tender(run, int(d.group(1)), d.group(2), d.group(3), int(d.group(4)), price)
                by_tid.setdefault(t.tid, []).append(t)
                reason = d.group(7)
                pps, bid = PPS.search(reason), BID.search(reason)
                t.decisions.append(Decision(w, d.group(6) == "ACCEPT", float(pps.group(1)) if pps else None,
                                            float(bid.group(1)) if bid else None, reason, price))
                continue
            r = REJECT.search(msg)
            if r:
                tid, bid = int(r.group(1)), float(r.group(2))
                for t in reversed(by_tid.get(tid, [])):          # the bot that just bid that price
                    if any(dd.bid == bid for dd in t.decisions):
                        t.rejected.append((w, bid))
                        break
                continue
            if "action failed" in msg:
                run.tender_fail.append((w, msg))
    return run


def parse(path: str | Path) -> Run:
    path = Path(path)
    return parse_lines(path.read_text(errors="replace").splitlines(), path)
