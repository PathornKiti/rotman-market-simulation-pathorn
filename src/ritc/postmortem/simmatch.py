"""
Which simulator market produced a log? (`local` runs only.)

The simulator's tender stream - when each tender arrives, its stock, side, size and whether it has a
fixed price - is drawn from the seed alone, whatever the bot does (its trades only move prices). So a
seed is found by matching the logged tenders against the stream of each candidate seed:

* a dry run sends no orders, so tender ids are 1, 2, 3, ... in arrival order: id k must be stream[k-1];
* a live run's ids interleave with order ids, so the logged tenders must appear IN ORDER in the stream.

A log that matches no seed (or arrives at RIT's 1 tick per second with nothing matching) is `live`.
The market variant (benign, hostile, tenders priced off the visible mid, ...) is then read off the fixed
tender prices, which a dry run reproduces exactly.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor

from ..sim.server import Market
from .logparse import Run

PRIORITY = (list(range(1, 65)) + list(range(101, 133)) + list(range(201, 233)) + list(range(301, 333))
            + list(range(401, 433)) + [777, 0, 42])


class _Quiet(Market):
    """The market alone: no fills to compute, no time and sales."""

    def _print_flow(self):
        pass

    def _fill_resting(self):
        pass


def stream(seed: int, stress: str = "", hostile: float = 0.0, books: bool = False, books_all: bool = False) -> dict:
    """Tenders (with the hidden reserve / rival) and the mid path of one simulated heat with no bot."""
    old = os.environ.get("RITC_STRESS")
    os.environ["RITC_STRESS"] = stress
    try:
        m = _Quiet("liability", 420, 1, seed, hostile=hostile)
    finally:
        if old is None:
            os.environ.pop("RITC_STRESS", None)
        else:
            os.environ["RITC_STRESS"] = old
    m.status = "ACTIVE"
    tenders, mids, seen, all_books = [], [], set(), []
    while m.status == "ACTIVE":
        m.advance()
        if m.status != "ACTIVE":
            break
        mids.append({t: round(s.mid, 4) for t, s in m.secs.items()})
        if books_all:
            all_books.append({t: m.book_view(s, 20) for t, s in m.secs.items()})
        for tid, t in m.tenders.items():
            if tid in seen:
                continue
            seen.add(tid)
            s = m.secs[t["ticker"]]
            row = {"index": len(tenders) + 1, "tid": tid, "tick": m.abs_tick, "ticker": t["ticker"],
                   "action": t["action"], "qty": t["quantity"], "fixed": t["is_fixed_bid"], "price": t["price"],
                   "expires": t["expires"], "reserve": t["_reserve"], "rival": t["_rival"],
                   "kind": "private" if t["is_fixed_bid"] else
                   ("wta" if t["caption"].startswith("Winner") else "auction"),
                   "caption": t["caption"], "mid": s.mid}
            if books:
                row["book"] = m.book_view(s, 20)
            tenders.append(row)
    return {"seed": seed, "stress": stress, "hostile": hostile, "tenders": tenders, "mids": mids, "books": all_books}


def _sig(t) -> tuple:
    return (t["ticker"], t["action"], t["qty"], t["fixed"])


def score(run_keys: list[tuple], tenders: list[dict], positional: bool) -> set:
    """
    Logged tenders this stream explains. Dry run: tender id k must be the k-th tender (a random seed
    matches ~1 in 48 by chance). Live run: the logged tenders must appear in order (a random seed
    matches about a third this way, so only a near-complete match counts).
    """
    if positional:
        return {k for k in run_keys
                if 0 < k[0] <= len(tenders) and _sig(tenders[k[0] - 1]) == (k[2], k[1], k[3], k[4])}
    hit, j = set(), 0
    for k in sorted(run_keys):
        sig = (k[2], k[1], k[3], k[4])
        i = j
        while i < len(tenders) and _sig(tenders[i]) != sig:
            i += 1
        if i < len(tenders):
            hit.add(k)
            j = i + 1
    return hit


def _job(args):
    seed, keys, positional = args
    return seed, score(keys, stream(seed)["tenders"], positional)


def run_keys(run: Run) -> list[tuple]:
    return sorted((t.tid, t.action, t.ticker, t.qty, t.fixed) for t in run.tenders.values())


def find_seeds(run: Run, seeds: range | list[int] = range(0, 3000), workers: int | None = None) -> list[dict]:
    """Seeds that together explain the logged tenders, best first: [{"seed", "keys"}]. [] = no match."""
    keys = run_keys(run)
    if not keys:
        return []
    positional = bool(run.dry_run) and not run.orders

    def good(hit: set, covered: set) -> bool:
        if positional:
            return len(hit - covered) >= max(3, 0.25 * len(keys))
        return len(hit) >= max(3, 0.9 * len(keys))

    pool_seeds = set(seeds)
    order = [s for s in PRIORITY if s in pool_seeds] + sorted(pool_seeds - set(PRIORITY))
    found, covered = [], set()
    for s in order[:len([p for p in PRIORITY if p in pool_seeds])]:      # the usual seeds first
        hit = score(keys, stream(s)["tenders"], positional)
        if good(hit, covered):
            found.append({"seed": s, "keys": hit})
            covered |= hit
            if len(covered) >= len(keys) or not positional:
                return found
    rest = order[len([p for p in PRIORITY if p in pool_seeds]):]
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for s, hit in pool.map(_job, [(s, keys, positional) for s in rest], chunksize=32):
            if len(hit) >= 3:
                results.append((len(hit), s, hit))
    results.sort(key=lambda r: (-r[0], r[1]))
    for _, s, hit in results:
        if good(hit, covered):
            found.append({"seed": s, "keys": hit})
            covered |= hit
            if len(covered) >= len(keys) or not positional:
                break
    return found


def match_scenarios(run: Run, seed: int, keys: set, scenarios: dict) -> list[dict]:
    """
    Market variants (stress scenarios) whose fixed tender prices match the log. Only tenders seen before
    the bot's first order count: after that our own trades move prices.
    """
    first_order = min((o[0] for o in run.orders), default=None)
    logged = {}
    for t in run.tenders.values():
        if not t.fixed or (t.tid, t.action, t.ticker, t.qty, t.fixed) not in keys:
            continue
        walls = [d.wall for d in t.decisions] + ([t.seen_wall] if t.seen_wall else [])
        if first_order is not None and walls and min(walls) > first_order:
            continue
        logged[(t.tid, t.action, t.ticker, t.qty)] = set(t.prices)
    if not logged:
        return []
    out = []
    for name, (stress, hostile) in scenarios.items():
        tenders = stream(seed, stress, hostile)["tenders"]
        by_key = {}
        for tt in tenders:
            by_key.setdefault((tt["ticker"], tt["action"], tt["qty"]), []).append(tt)
        hit = 0
        for (tid, action, ticker, qty), prices in logged.items():
            cand = [tenders[tid - 1]] if 0 < tid <= len(tenders) else []
            cand = [c for c in cand if (c["ticker"], c["action"], c["qty"]) == (ticker, action, qty)] or \
                by_key.get((ticker, action, qty), [])
            if any(c["price"] is not None and any(abs(c["price"] - p) < 1e-6 for p in prices) for c in cand):
                hit += 1
        out.append({"scenario": name, "stress": stress, "hostile": hostile, "matched": hit, "of": len(logged)})
    out.sort(key=lambda r: -r["matched"])
    return out


def align(keys: set | list, tenders: list[dict], positional: bool) -> dict[tuple, int]:
    """Logged tender key -> index (0-based) of the same tender in the stream."""
    out = {}
    if positional:
        for k in keys:
            if 0 < k[0] <= len(tenders) and _sig(tenders[k[0] - 1]) == (k[2], k[1], k[3], k[4]):
                out[k] = k[0] - 1
        return out
    j = 0
    for k in sorted(keys):
        sig = (k[2], k[1], k[3], k[4])
        i = j
        while i < len(tenders) and _sig(tenders[i]) != sig:
            i += 1
        if i < len(tenders):
            out[k] = i
            j = i + 1
    return out


# Scenarios that move tender prices before the bot trades (others look exactly like the base market).
PRICE_DISTINCT = ("base (brief)", "volatility x2", "worse tenders -10c", "hostile anchored", "hostile crowd at expiry",
                  "everything anchored", "real-world + hostile", "vol regimes (hidden)")


def cover_variants(run: Run, seed: int, keys: set, scenarios: dict, max_variants: int = 4) -> list[dict]:
    """
    Smallest set of market variants that explains the logged fixed prices (several bots in one log can run
    different variants of the same seed). Each item: scenario, stress, hostile, matched (key, price) pairs.
    """
    first_order = min((o[0] for o in run.orders), default=None)
    pairs = set()
    for t in run.tenders.values():
        if not t.fixed or (t.tid, t.action, t.ticker, t.qty, t.fixed) not in keys:
            continue
        walls = [d.wall for d in t.decisions] + ([t.seen_wall] if t.seen_wall else [])
        if first_order is not None and walls and min(walls) > first_order:
            continue
        pairs |= {((t.tid, t.action, t.ticker, t.qty), p) for p in t.prices}
    names = [n for n in PRICE_DISTINCT if n in scenarios]
    expl = {}
    for n in names:
        stress, hostile = scenarios[n]
        tenders = stream(seed, stress, hostile)["tenders"]
        got = set()
        for (k, p) in pairs:
            c = tenders[k[0] - 1] if 0 < k[0] <= len(tenders) else None
            cands = [c] if c and (c["ticker"], c["action"], c["qty"]) == (k[2], k[1], k[3]) else \
                [x for x in tenders if (x["ticker"], x["action"], x["qty"]) == (k[2], k[1], k[3])]
            if any(x["price"] is not None and abs(x["price"] - p) < 1e-6 for x in cands):
                got.add((k, p))
        expl[n] = got
    out, covered = [], set()
    while len(out) < max_variants and expl:
        best = max(expl, key=lambda n: (len(expl[n] - covered), n == "base (brief)"))
        new = expl[best] - covered
        if len(new) < (1 if not out else 2):
            break
        stress, hostile = scenarios[best]
        out.append({"scenario": best, "stress": stress, "hostile": hostile, "matched": len(expl[best]),
                    "of": len(pairs)})
        covered |= expl.pop(best)
    if not out:
        stress, hostile = scenarios.get("base (brief)", ("", 0.0))
        out.append({"scenario": "base (brief)", "stress": stress, "hostile": hostile, "matched": 0, "of": len(pairs)})
    return out
