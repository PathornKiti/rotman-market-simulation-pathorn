"""
What a run's journal (logs/<name>.jsonl, core/journal.py) says: the P&L path, where it came from, and a
distribution of what the bot struggled with. This is the analysis for LIVE runs (the RIT server), where no
simulator can rebuild the market; local runs get it too when they have a journal.

Each struggle is {"count", "value" ($ at stake when it can be estimated), "severity", "detail", "hint"}; the
hint names the setting or improvement to look at. `learnings()` writes the summary as JSON
(reports/<run>_<mode>.json and reports/live-learnings.json across runs) so the next review can start from
every earlier run's numbers.
"""

from __future__ import annotations

import json
import statistics as st
from pathlib import Path

from ..core.journal import read

GROSS_LIMIT, NET_LIMIT = 250_000, 100_000
FINE1, FINE2 = 0.20, 0.40


def _mid(t: dict, tk: str) -> float | None:
    b, a = (t.get("bid") or {}).get(tk), (t.get("ask") or {}).get(tk)
    if b and a:
        return (b + a) / 2
    last = (t.get("last") or {}).get(tk)
    return float(last) if last else None


def _dense(ticks: list[dict], tickers: list[str]) -> list[dict]:
    """One point per tick from the first to the last journaled tick (slow loops skip ticks): carry the last value."""
    if not ticks:
        return []
    by = {t["tick"]: t for t in ticks}
    out, last = [], ticks[0]
    for k in range(ticks[0]["tick"], ticks[-1]["tick"] + 1):
        last = by.get(k, last)
        pos = last.get("pos") or {}
        out.append({"tick": k, "nlv": last.get("nlv"), "pos": {tk: pos.get(tk, 0) for tk in tickers}})
    return out


def analyse(path: str | Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    ev = read(path)
    if not ev:
        return None
    start = next((e for e in ev if e["k"] == "start"), {})
    end = next((e for e in reversed(ev) if e["k"] == "end"), None)
    ticks = [e for e in ev if e["k"] == "tick"]
    tickers = sorted({tk for t in ticks for tk in (t.get("pos") or {})})
    by_tick = {t["tick"]: t for t in ticks}
    orders = [e for e in ev if e["k"] == "order"]
    errors = [e for e in ev if e["k"] == "error"]
    slow = [e for e in ev if e["k"] == "slow_loop"]
    tenders: dict[int, dict] = {}
    for e in ev:
        if e["k"].startswith("tender_"):
            d = tenders.setdefault(e["tid"], {"tid": e["tid"]})
            d[e["k"][7:]] = e

    # ------------------------------------------------------------------ P&L path
    nlv = [(t["tick"], t["nlv"]) for t in ticks if t.get("nlv") is not None]
    final = end["nlv"] if end and end.get("nlv") is not None else (nlv[-1][1] if nlv else None)
    peak, dd, dd_tick = float("-inf"), 0.0, None
    for k, v in nlv:
        peak = max(peak, v)
        if peak - v > dd:
            dd, dd_tick = peak - v, k
    inventory = 0.0                                  # position x mid change, tick to tick
    for a, b in zip(ticks, ticks[1:]):
        for tk in tickers:
            m0, m1 = _mid(a, tk), _mid(b, tk)
            if m0 is not None and m1 is not None:
                inventory += (a.get("pos") or {}).get(tk, 0) * (m1 - m0)

    # ------------------------------------------------------------------ tenders
    rows = []
    for tid, d in sorted(tenders.items()):
        seen, dec, ans = d.get("seen", {}), d.get("decision"), d.get("answer")
        if not seen:
            continue
        sign = 1 if str(seen.get("action")).upper() == "BUY" else -1
        price = seen.get("price") if seen.get("fixed", True) else (dec or {}).get("price")
        mid = (dec or {}).get("mid") or seen.get("mid")
        edge = sign * (mid - price) if (mid and price) else None
        ok = bool(ans and ans.get("answer") == "accept" and not (isinstance(ans.get("resp"), dict)
                                                                   and ans["resp"].get("success") is False))
        booked = None
        if ok:
            t0, qty, tk = ans["tick"], int(seen.get("qty") or 0), seen.get("ticker")
            horizon = max(int(seen.get("expires") or t0) + 3, t0 + 5)
            prev = (by_tick.get(t0) or {}).get("pos", {}).get(tk)
            for k in range(t0 + 1, horizon + 1):
                cur = (by_tick.get(k) or {}).get("pos", {}).get(tk)
                if prev is not None and cur is not None and abs((cur - prev) - sign * qty) <= max(2000, 0.2 * qty):
                    booked = k
                    break
                if cur is not None:
                    prev = cur
        rows.append({"tid": tid, "tick": seen.get("tick"), "ticker": seen.get("ticker"), "action": seen.get("action"),
                     "qty": seen.get("qty"), "fixed": seen.get("fixed", True), "price": price,
                     "expires": seen.get("expires"), "caption": seen.get("caption"), "mid": mid, "edge": edge,
                     "decision_tick": (dec or {}).get("tick"), "accept": (dec or {}).get("accept"),
                     "pps": (dec or {}).get("pps"), "reason": (dec or {}).get("reason"),
                     "answer": (ans or {}).get("answer"), "success": ok, "booked_tick": booked,
                     "left": (int(seen["expires"]) - int(dec["tick"])) if dec and seen.get("expires") else None})
    edge_total = sum(r["edge"] * r["qty"] for r in rows if r["booked_tick"] and r["edge"] is not None)
    other = (final - edge_total - inventory) if final is not None else None

    # ------------------------------------------------------------------ struggles
    S: dict[str, dict] = {}

    def add(name, count, detail, hint, value=None, severity="medium"):
        if count:
            S[name] = {"count": count, "value": value, "severity": severity, "detail": detail, "hint": hint}

    last_dec = max((r["decision_tick"] or 0 for r in rows), default=0)
    unanswered = [r for r in rows if r["answer"] is None and (r["tick"] or 0) <= last_dec]
    add("tender unanswered", len(unanswered), f"{len(unanswered)} tenders seen but never answered",
        "loops too slow for decide_late_ticks: the answer-deadline safety net (POSTMORTEM.md)", severity="high",
        value=sum(max(0.0, r["edge"] or 0) * r["qty"] for r in unanswered))
    tight = [r for r in rows if r["left"] is not None and r["left"] <= 1]
    add("answered at the last tick", len(tight), "answers with <= 1 tick left in the window",
        "raise decide_late_ticks or speed up the loop")
    failed = [d for d in tenders.values() if (d.get("answer") or {}).get("answer") == "failed"]
    add("tender action failed", len(failed), "; ".join((d["answer"].get("msg") or "")[:80] for d in failed[:3]),
        "expired between read and answer, or LIMIT_EXCEEDED: check limits and timing", severity="high")
    rej = [r for r in rows if r["answer"] == "accept" and not r["success"]]
    add("auction bid rejected at once", len(rej), "competitive bids answered success=false",
        "record where reserves sit vs the mid; the margin is not the problem (POSTMORTEM.md)")
    lost = [r for r in rows if r["success"] and r["booked_tick"] is None]
    add("accepted, never booked", len(lost), "accepted/bid but no position jump of that size followed "
        "(winner-take-all lost, or booking slower than the window)", "check booking delays (booking_ticks)")
    delays = [r["booked_tick"] - r["decision_tick"] for r in rows if r["booked_tick"] and r["decision_tick"]]
    if delays and max(delays) > 3:
        add("slow booking", sum(1 for x in delays if x > 3), f"booking delay median {st.median(delays):.0f} ticks, "
            f"max {max(delays)}", "set booking_ticks to the delay + 1")
    big = [r for r in rows if r["accept"] is False and r["fixed"] and (r["edge"] or 0) > 0.10]
    add("declined 10c+ inside the mid", len(big), "private tenders declined while 10c+ better than the mid",
        "refill_factor 3 + min_profit 0 if the practice crowd check shows no crowd at expiry",
        value=sum(r["edge"] * r["qty"] for r in big))
    # front-running exposure: orders on a stock while one of its tenders is undecided
    windows = []
    for r in rows:
        if r["tick"] is None:
            continue
        end_t = r["decision_tick"] or r["expires"] or r["tick"]
        if not r["fixed"] and r["answer"] == "accept":
            end_t = max(end_t, r["expires"] or end_t)
        windows.append((r["ticker"], r["tick"], end_t))
    fr = [o for o in orders if any(o["ticker"] == tk and a <= o.get("tick", -1) < b for tk, a, b in windows)]
    add("orders in an undecided window", len(fr), f"{sum(o['qty'] for o in fr):,} shares sent while a tender on "
        "that stock was undecided", "front-running on the strict reading: respect_windows / freeze_rejected_bids",
        severity="high")
    flips = 0
    for a, b in zip(ticks, ticks[1:]):
        for tk in tickers:
            p0, p1 = (a.get("pos") or {}).get(tk, 0), (b.get("pos") or {}).get(tk, 0)
            if p0 and p1 and (p0 > 0) != (p1 > 0) and not any(r["ticker"] == tk and r["booked_tick"] == b["tick"]
                                                             for r in rows):
                flips += 1
    add("position through zero", flips, "a position changed sign without a tender booking that tick",
        "speculation fine $0.20-0.40/share: unwinds must never pass zero", severity="high")
    near = sum(1 for t in ticks if sum(abs(v) for v in (t.get("pos") or {}).values()) > 0.9 * GROSS_LIMIT
               or abs(sum((t.get("pos") or {}).values())) > 0.9 * NET_LIMIT)
    add("near the limits", near, "ticks above 90% of the 250k gross or 100k net limit",
        "limits are enforced: tenders get refused; see risk room")
    by_type: dict[str, int] = {}
    for e in errors:
        m = e.get("msg", "")
        kind = "rate limited (429)" if "429" in m else "connection" if "onnection" in m else e.get("where", "other")
        by_type[kind] = by_type.get(kind, 0) + 1
    for kind, n in by_type.items():
        add(f"API error: {kind}", n, f"{n} errors", "RIT_MIN_INTERVAL for 429s; keep the bot next to the client",
            severity="high" if n > 5 else "medium")
    loop = [t.get("loop_ms") or 0 for t in ticks]
    add("slow loops", len(slow), f"{len(slow)} loops > 500 ms; slowest loop per tick p95 "
        f"{sorted(loop)[int(0.95 * (len(loop) - 1))] if loop else 0:.0f} ms",
        "slow loops cost money even when every tender is answered: antivirus / network", severity="medium")
    if dd > 0.25 * max(abs(final or 0), 1) and dd > 5000:
        add("drawdown", 1, f"max drawdown ${dd:,.0f} at tick {dd_tick}", "see the P&L path and positions then",
            value=-dd)
    held = {tk: (ticks[-1].get("pos") or {}).get(tk, 0) for tk in tickers} if ticks else {}
    gross_end = sum(abs(v) for v in held.values())
    if gross_end:
        add("held at the bell", 1, f"{gross_end:,} shares open at the last tick ({held})",
            "closed at the last price: fine-free per the brief (confirm A15)", severity="low")
    return {
        "journal": str(path), "start": start, "end": end, "tickers": tickers, "final_pnl": final,
        "max_drawdown": dd, "drawdown_tick": dd_tick, "parts": {"tender edge at decision": edge_total,
                                                             "inventory moves": inventory,
                                                             "execution, close-out, fines, other": other},
        "series": _dense(ticks, tickers),
        "tenders": rows, "orders": len(orders), "order_shares": sum(o["qty"] for o in orders),
        "struggles": S, "loop_p95": sorted(loop)[int(0.95 * (len(loop) - 1))] if loop else None,
    }


def learnings(stamp: str, mode: str, j: dict) -> dict:
    """The compact, machine-readable summary of one run (for the next review)."""
    rows = j["tenders"]
    return {"run": stamp, "mode": mode, "case": (j.get("start") or {}).get("case"),
            "dry_run": (j.get("start") or {}).get("dry_run"), "final_pnl": j["final_pnl"],
            "max_drawdown": j["max_drawdown"], "parts": j["parts"],
            "tenders": {"seen": len(rows), "answered": sum(1 for r in rows if r["answer"]),
                        "accepted": sum(1 for r in rows if r["accept"]),
                        "booked": sum(1 for r in rows if r["booked_tick"])},
            "struggles": {k: {kk: v[kk] for kk in ("count", "value", "severity", "hint")}
                          for k, v in j["struggles"].items()},
            "loop_p95_ms": j["loop_p95"], "orders": j["orders"]}


def write_learnings(out_dir: Path, stamp: str, mode: str, stem: str, j: dict) -> Path:
    one = learnings(stamp, mode, j)
    p = out_dir / f"{stem}_{mode}.json"
    p.write_text(json.dumps(one, indent=1, default=str))
    allp = out_dir / "live-learnings.json"
    try:
        runs = json.loads(allp.read_text()) if allp.exists() else []
    except ValueError:
        runs = []
    runs = [r for r in runs if r.get("run") != stamp] + [one]
    allp.write_text(json.dumps(sorted(runs, key=lambda r: r["run"]), indent=1, default=str))
    return p
