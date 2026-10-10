"""
Gap checks for one run of the `liability` bot against the Liquidity Risk Case brief, and the catalogue of
improvements they point to.

Each check reads the parsed log and, for `local` runs, the simulator's ground truth for that seed (true mid,
hidden auction reserve, winner-take-all rival, decision window), and returns a status:

    ok      nothing to fix here
    gap     money or fine risk left on the table in THIS run
    fixed   the gap shows in this (older) log but the current bot no longer has it
    info    context needed to read the run (e.g. the simulator shut down at the bell)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .logparse import Run

BRIEF = "document/Liquidity Risk Case - for selection 2027.pdf"
VERSIONS = {
    "v1-instant": "v1 (answers each tender the moment it appears)",
    "v2-late": "v2 (answers 3 ticks before expiry, no end-of-heat guard)",
    "v3-late-guard": "v3 (answers 3 ticks before expiry + end-of-heat guard) = current",
}


@dataclass
class Check:
    id: str
    title: str
    clause: str                 # what the brief says
    status: str                 # ok | gap | fixed | info
    detail: str
    evidence: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    value: float | None = None  # $ at stake in this run, when it can be put in dollars


# ------------------------------------------------------------------------------------------- improvements
# Evidence: paired simulator A/B on the current bot (16-32 seeds, same market for every setting). "anchored"
# hostile = other desks manipulate and crowd tenders, tenders priced off the VISIBLE mid (the realistic one).
IMPROVEMENTS: dict[str, dict] = {
    "start-early": {
        "title": "Start the bot before the heat opens",
        "area": "operations", "status": "do now",
        "what": "Launch `ritc run liability --live` while the case is still PAUSED. The runner waits for ACTIVE, so "
                "starting early costs nothing; joining late forfeits every tender that arrives before the bot does.",
        "evidence": "Run 20261009-224656 joined at about tick 106 of 420 and never saw tenders 1-2 (25% of the heat).",
    },
    "auction-freeze": {
        "title": "Treat an auction bid as undecided until its window closes",
        "area": "fines", "status": "fixed (freeze_rejected_bids)",
        "what": "A competitive bid stays in the decision window until expiry even if the server answers "
                "success=false at once, so the stock is frozen until then (front-running rule).",
        "evidence": "v1 logs traded the stock straight after a rejected bid, inside the bid's window. "
                    "Strict front-running simulator: +$9.6k (t 3.0) when fixed (docs/RESEARCH.md).",
    },
    "end-guard": {
        "title": "Never wait past the last tick a tender can still be accepted",
        "area": "tender timing", "status": "fixed (decide_late_end_guard)",
        "what": "When a window runs past the bell, answer before the bot's own cut-off instead of 3 ticks before "
                "`expires`.",
        "evidence": "v2 run 20261010-104620 left 2 of 31 tenders unanswered at the end; v3 run 20261010-105313 on "
                    "the same market answered 31 of 31. Seeds 1-16 +$3.7k (t 4.1).",
    },
    "no-overshoot": {
        "title": "Never let resting and crossing orders together take a position through zero",
        "area": "fines", "status": "fixed (QuoteManager never_larger, unwind caps)",
        "what": "Shares that flip a position open a NEW position: speculation, fined $0.20-$0.40/share.",
        "evidence": "v1 run 20261009-224656 overshot CRZY by 2,400 shares (fine ~$480 plus a round trip). The "
                    "current bot opened 0 speculative shares in every replay of these markets.",
    },
    "refill-3": {
        "title": "Re-test a less conservative exit valuation (refill_factor 2 -> 3, min_profit 0.01 -> 0)",
        "area": "tender selection", "status": "candidate, gated on the practice crowd check",
        "what": "The bot prices a tender by walking a 2x book with taker fees, but it actually exits mostly by "
                "resting orders and the free close-out. It declines 30-50k blocks that are 10c+ through the mid. "
                "Both settings were rejected earlier for losing in the OLD hostile simulator (tenders priced off "
                "the true price, a known artifact).",
        "evidence": "refill 3 + min_profit 0, paired: seeds 1-32 base +$4.5k (t 3.40), holdout 101-132 +$3.6k "
                    "(t 2.99), "
                    "worst seed unchanged; realistic hostile +$0.3k / +$1.3k; thin books +$7.1k (t 4.91); but crowd at "
                    "expiry -$3.5k with the worst heat -$27k -> -$53k on seeds 1-32. Switch: --set "
                    "strategy.refill_factor=3 --set strategy.min_profit_per_share=0.",
    },
    "consistent-valuation": {
        "title": "Value the part of a block the bot will hold at the mid (execution-consistent valuation)",
        "area": "tender selection", "status": "conditional on practice evidence",
        "what": "Price the shares the hold budget lets the bot rest/hold at mid - 1c and walk the book only for "
                "the excess it will cross. Biggest gain in calm markets, biggest loss if other desks unwind the "
                "SAME private blocks at expiry. The brief says private tenders are 'assigned directly to "
                "individual participants' and come 'at different times', so check in practice whether private "
                "tenders show a crowd at all (ritc calibrate crowd event study, by tender type).",
        "evidence": "16 seeds: base +$3.5k (t 1.56), hostile anchored +$2.3k (t 1.82), crowd at expiry -$9.3k "
                    "(t -2.69, worst -$27k -> -$55k).",
    },
    "auction-bids": {
        "title": "Keep auction bids at value minus margin; learn the reserve, don't bid off the mid",
        "area": "auctions", "status": "tested - keep current",
        "what": "Winning every auction at its hidden reserve would be worth ~$2k per lost auction in a calm market, "
                "but a bid priced off the mid overpays (winner-take-all rivals, crowds of auction winners). "
                "Use the practice heats to measure where reserves sit (each success / failure brackets one).",
        "evidence": "Mid-based auction bids, 16 seeds: base -$1.2k (t -1.36), hostile anchored -$0.5k (t -2.86), "
                    "crowd at expiry -$3.7k.",
    },
    "deadline-safety": {
        "title": "Answer now if the NEXT loop would land past the window",
        "area": "tender timing", "status": "recommended (free insurance)",
        "what": "Answering 3 ticks before expiry assumes a loop takes well under 3 ticks. A safety net that answers "
                "early only when the measured ticks per loop say the NEXT loop would land past the window keeps the "
                "late-answer option at normal speed and only fires when the bot is too slow. Separately: slow loops "
                "cost money even when every tender is answered, so run the bot next to the RIT client and keep "
                "antivirus HTTP inspection off localhost.",
        "evidence": "Dry run 20261010-111656 (simulator at ~19 ticks/s, ~5 ticks per loop) answered 20 of 31 "
                    "tenders. A/B: identical at normal speed (64 seeds, both markets); 4 ticks per loop +$9.8k/heat "
                    "(76% -> 99% answered), 5 ticks per loop +$17.8k (57% -> 95%).",
    },
    "late-accept": {
        "title": "Keep answering tenders in the last 5 seconds",
        "area": "tender selection", "status": "recommended (passes on 64 fresh seeds)",
        "what": "The brief closes open positions at the last traded price with no fine, so a tender that arrives in "
                "the wind-down with edge vs the mid is profit even if it is never unwound.",
        "evidence": "Seeds 33-96: +$0.9k/heat (t 3.14), hostile +$0.1k, worst heat unchanged; seeds 1-32: +$0.4k "
                    "(t 1.53), worst unchanged in 5 scenarios. Change: call handle_tenders in wind_down when "
                    "close_hold_ticks is on, and min_ticks_to_unwind = 1.",
    },
    "passive-placement": {
        "title": "Where to rest the unwind: keep the current placement for now",
        "area": "execution", "status": "tested - keep current; practice-day measurement",
        "what": "Resting fills earn less than nothing on average in these replays: when the price jumps through a "
                "resting order it fills at the old price (adverse selection). Resting further from the touch "
                "did not fix it in the simulator (fewer fills, worse tails).",
        "evidence": "32 seeds: never stepping inside the spread +$0.5k (t 0.52) but worst heat $20.0k -> $5.6k; "
                    "resting one tick behind the touch +$0.2k (t 0.12), worst $20.0k -> -$4.9k. The simulator fills "
                    "resting orders only at the touch, so real fill data (ritc record / calibrate fill model) "
                    "must decide.",
    },
    "wta-shading": {
        "title": "Winner-take-all: measure rival bids in practice before shading",
        "area": "auctions", "status": "practice-day item",
        "what": "Only the best bid past the reserve wins, so it is a first-price auction against the other teams. "
                "Too few outcomes in one heat to learn from; record wins/losses and the clearing level in practice.",
        "evidence": "Seed 777: one winner-take-all bid cleared the reserve but lost to a better rival bid.",
    },
}


# ------------------------------------------------------------------------------------------------- checks
def _fmt_money(x: float) -> str:
    if abs(x) < 0.5:
        return "$0"
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def coverage(run: Run, streams: list[tuple]) -> Check:
    """streams: [(seed, stream tenders, {key: index})]."""
    clause = "The case runs for 420 seconds."
    title = "Was the bot running for the whole heat?"
    if not streams:
        c = run.clock()
        first = run.tick_at(run.t0) if c else None
        return Check("coverage", title, clause, "info", f"Log spans {run.duration:.0f} s of wall time"
                     + (f", from about tick {first}" if first is not None else "") + ".")
    missed, firsts, after = [], [], []
    for seed, tenders, aligned in streams:
        if not aligned:
            continue
        i0, i1 = min(aligned.values()), max(aligned.values())
        firsts.append((seed, i0 + 1, tenders[i0]["tick"]))
        missed += [(seed, t) for t in tenders[:i0]]
        after += [(seed, t) for t in tenders[i1 + 1:] if t["tick"] <= 414]
    if after and run.shutdown is False and run.api_errors == [] and not run.crashed:
        missed += after
    if missed:
        lost = sum(max(0.0, (t["mid"] - t["price"]) * (1 if t["action"] == "BUY" else -1)) * t["qty"]
                   for _, t in missed if t["fixed"])
        n_after = sum(1 for x in missed if x in after)
        return Check("coverage", title, clause, "gap",
                     "; ".join(f"seed {s}: first tender seen is #{i} (tick {k})" for s, i, k in firsts)
                     + f". {len(missed) - n_after} tender(s) arrived before the bot was running"
                     + (f" and {n_after} after the log stops (the run was stopped before the bell)" if n_after else "")
                     + ".",
                     [f"tender #{t['index']} {t['action']} {t['ticker']} x{t['qty']} at tick {t['tick']}"
                      + (f" @ {t['price']} "
                         f"({100 * (t['mid'] - t['price']) * (1 if t['action'] == 'BUY' else -1):+.0f}c "
                         f"vs the mid)" if t["fixed"] else f" ({t['kind']})") for _, t in missed],
                     ["start-early"], value=lost)
    return Check("coverage", title, clause, "ok",
                 "; ".join(f"seed {s}: first tender (#{i}) seen at tick {k}" for s, i, k in firsts)
                 + ": nothing missed at the start.")


def answered(run: Run) -> Check:
    tl = run.tender_list()
    seen = [t for t in tl if t.seen_tick is not None or t.decisions]
    unanswered = [t for t in seen if not t.decisions]
    clause = "Each tender comes with a short decision window that may vary by security."
    if not seen:
        return Check("answered", "Did the bot answer every tender it saw?", clause, "info", "No tenders in this log.")
    if not unanswered:
        left = sorted(t.expires - run.tick_at(t.decisions[0].wall) for t in seen
                      if t.expires is not None and t.decisions and run.clock())
        return Check("answered", "Did the bot answer every tender it saw?", clause, "ok",
                     f"{len(seen)} of {len(seen)} tenders answered"
                     + (f", median {left[len(left) // 2]} ticks before expiry (range {left[0]}-{left[-1]})." if left
                        else " the moment they appeared (v1)." if run.version == "v1-instant" else "."))
    ev, at_end = [], 0
    c = run.clock()
    speed = c[1] if c else None
    for t in unanswered:
        why = ""
        if t.expires is not None and t.expires > 414:
            why, at_end = "window runs past the bot's last decision tick", at_end + 1
        elif speed and speed > 3:
            why = f"simulator ran {speed:.0f} ticks/s: ~{speed * 0.25:.0f} ticks pass per bot loop"
        ev.append(f"tender {t.tid} {t.action} {t.ticker} x{t.qty} seen at tick {t.seen_tick}, expires {t.expires}"
                  + (f" - {why}" if why else ""))
    status = "fixed" if run.version == "v2-late" and at_end == len(unanswered) else "gap"
    imp = ["end-guard"] if at_end else ["deadline-safety"]
    detail = f"{len(unanswered)} of {len(seen)} tenders seen were never answered."
    if speed and speed > 3:
        detail += (f" The simulator ran at about {speed:.0f} ticks per second, so 3 ticks before expiry was shorter "
                   "than one bot loop. At RIT's 1 tick per second this does not happen, but it shows the "
                   "margin's limit.")
    return Check("answered", "Did the bot answer every tender it saw?", clause, status, detail, ev, imp)


def auctions(run: Run, gt: dict) -> Check:
    clause = ("Competitive auctions fill any bid past a hidden reserve, at the bid. Winner-take-all goes to the best "
              "bid that meets the reserve.")
    rows = []
    for t in run.tender_list():
        if t.fixed or not t.decisions:
            continue
        g = gt.get((t.tid, t.action, t.ticker, t.qty, t.fixed))
        for d in t.decisions:
            if d.bid is None:
                continue
            sign = 1 if t.action == "BUY" else -1
            row = {"tid": t.tid, "ticker": t.ticker, "action": t.action, "qty": t.qty, "bid": d.bid,
                   "rejected": any(abs(b - d.bid) < 1e-9 for _, b in t.rejected), "kind": g["kind"] if g else "?"}
            if g:
                row.update(reserve=g["reserve"], rival=g["rival"], vs_reserve=sign * (d.bid - g["reserve"]),
                           vs_rival=sign * (d.bid - g["rival"]) if g["rival"] is not None else None,
                           mid=g["mid"])
                row["won"] = row["vs_reserve"] >= -1e-9 and (row["vs_rival"] is None or row["vs_rival"] > 1e-9)
            rows.append(row)
    if not rows:
        return Check("auctions", "How did the auction bids do?", clause, "info", "No auction or winner-take-all bids.")
    won = [r for r in rows if r.get("won")]
    short = [r for r in rows if r.get("vs_reserve") is not None and r["vs_reserve"] < 0]
    rival = [r for r in rows if r.get("vs_reserve") is not None and r["vs_reserve"] >= 0 and not r.get("won")]
    ev = [f"tender {r['tid']} {r.get('kind', '?')} {r['action']} {r['ticker']} x{r['qty']}: bid {r['bid']:.2f}"
          + (f", hidden reserve {r['reserve']:.2f} ({'+' if r['vs_reserve'] >= 0 else ''}{100 * r['vs_reserve']:.0f}c)"
             if r.get("reserve") is not None else "")
          + (f", rival {r['rival']:.2f}" if r.get("rival") is not None else "")
          + (" - WON" if r.get("won") else (" - rejected at once" if r["rejected"] else " - lost")) for r in rows]
    detail = (f"{len(rows)} bids: {len(won)} would win, {len(short)} short of the hidden reserve, {len(rival)} "
              f"past the "
              f"reserve but beaten by a rival.")
    if short:
        gap_c = sorted(-100 * r["vs_reserve"] for r in short)
        detail += f" Shortfalls: median {gap_c[len(gap_c) // 2]:.0f}c, smallest {gap_c[0]:.0f}c."
    return Check("auctions", "How did the auction bids do?", clause, "gap" if short or rival else "ok", detail, ev,
                 ["auction-bids", "wta-shading"])


def front_running(run: Run, gt: dict) -> Check:
    clause = ("Trades executed during a tender's decision window, before the decision is final, are front-running "
              "(fined like speculation).")
    if not run.orders:
        return Check("front-running", "Any orders inside an undecided window?", clause, "info",
                     "No orders in this log (dry run).")
    if not gt:
        return Check("front-running", "Any orders inside an undecided window?", clause, "info",
                     "No ground truth for windows (live run without a recording).")
    c = run.clock()
    if c is None:
        return Check("front-running", "Any orders inside an undecided window?", clause, "info", "No tick clock.")
    windows = []                  # (ticker, start tick, end tick, why)
    by_key = gt
    for t in run.tender_list():
        k = (t.tid, t.action, t.ticker, t.qty, t.fixed)
        g = by_key.get(k)
        if g is None:
            continue
        ans = min((d.wall for d in t.decisions), default=None)
        ans_tick = run.tick_at(ans) if ans is not None else g["expires"]
        if ans_tick > g["tick"]:
            windows.append((t.ticker, g["tick"], min(ans_tick, g["expires"]), f"tender {t.tid} not yet answered"))
        if not t.fixed and any(d.bid is not None for d in t.decisions):
            windows.append((t.ticker, ans_tick, g["expires"], f"our bid on tender {t.tid} until expiry"))
    hits = []
    for w, _mode, side, ticker, qty, px in run.orders:
        k = run.tick_at(w)
        for tk, a, b, why in windows:
            if tk == ticker and a < k < b:
                hits.append((k, side, ticker, qty, px, why))
                break
    if not hits:
        return Check("front-running", "Any orders inside an undecided window?", clause, "ok",
                     f"None of {len(run.orders)} order submissions fell inside an undecided window on its stock.")
    sh = sum(h[3] for h in hits)
    fixed = run.version != "v3-late-guard" and all("our bid" in h[5] for h in hits)
    why_hits = "unwinds sent while our own auction bid was still pending" if fixed else "listed below"
    return Check("front-running", "Any orders inside an undecided window?", clause, "fixed" if fixed else "gap",
                 f"{len(hits)} order submissions ({sh:,} shares sent, not all filled) inside a window, on the strict "
                 f"reading of the brief. Most are {why_hits}.",
                 [f"tick {h[0]}: {h[1]} {h[2]} {h[3]:,} @ {h[4]:.2f} ({h[5]})" for h in hits[:12]]
                 + ([f"... {len(hits) - 12} more"] if len(hits) > 12 else []), ["auction-freeze"])


def overshoot(run: Run, gt: dict | None = None) -> Check:
    clause = "Opening any new position is fined: $0.20/share for the first 5,000 shares, $0.40 beyond."
    if not run.blocks:
        return Check("speculation", "Did any unwind take a position through zero?", clause, "info",
                     "No unwinds in this log.")
    def took(t, d) -> bool:
        if not d.accept or any(abs(b - (d.bid or -1)) < 1e-9 for _, b in t.rejected):
            return False
        g = (gt or {}).get((t.tid, t.action, t.ticker, t.qty, t.fixed))
        if g and d.bid is not None:                          # an auction bid the client would not take
            sign = 1 if t.action == "BUY" else -1
            if sign * (d.bid - g["reserve"]) < 0 or (g["rival"] is not None and sign * (d.bid - g["rival"]) <= 0):
                return False
        return True
    accepts = sorted((d.wall, t.ticker) for t in run.tenders.values() for d in t.decisions if took(t, d))
    events = []
    last: dict[str, tuple[float, int]] = {}
    for w, kind, ticker, start, _target, _deadline, _ in run.blocks:
        if kind != "start" or start == 0:
            continue
        prev = last.get(ticker)
        if prev and (prev[1] > 0) != (start > 0):
            new_tender = any(prev[0] < aw <= w and at == ticker for aw, at in accepts)
            if not new_tender:
                events.append((w, ticker, prev[1], start))
        last[ticker] = (w, start)
    if not events:
        return Check("speculation", "Did any unwind take a position through zero?", clause, "ok",
                     "No position flipped sign without a new tender.")
    sh = sum(abs(e[3]) for e in events)
    return Check("speculation", "Did any unwind take a position through zero?", clause,
                 "fixed" if run.version == "v1-instant" else "gap",
                 f"{len(events)} unwind(s) overshot zero by {sh:,} shares in total: fined as a new position "
                 f"(about {_fmt_money(0.2 * min(sh, 5000) + 0.4 * max(0, sh - 5000))}) plus the round trip back.",
                 [f"{e[1]}: block was {e[2]:+,} and the next started at {e[3]:+,} with no new tender" for e in events],
                 ["no-overshoot"], value=-(0.2 * min(sh, 5000) + 0.4 * max(0, sh - 5000)))


def valuation(run: Run, gt: dict) -> Check:
    clause = ("Objective 1: evaluate profitability from the limit order book; accept tenders expected to make money, "
              "reject unattractive ones.")
    if not gt:
        return Check("valuation", "Were profitable tenders declined?", clause, "info", "No ground truth (live run).")
    big, bad, close = [], [], []
    for t in run.tender_list():
        k = (t.tid, t.action, t.ticker, t.qty, t.fixed)
        if not t.fixed or k not in gt:
            continue
        g = gt[k]
        sign = 1 if t.action == "BUY" else -1
        for d in t.decisions:
            if d.price is None:
                continue
            edge = sign * (g["mid"] - d.price)
            if not d.accept and edge > 0.10:
                big.append((t, d, edge))
            elif not d.accept and edge > 0 and d.pps is not None and d.pps > -0.02:
                close.append((t, d, edge))
            elif d.accept and edge < 0:
                bad.append((t, d, edge))
    ev = [f"declined tender {t.tid} {t.action} {t.ticker} x{t.qty} @ {d.price}: {100 * e:+.0f}c through the mid on "
          f"arrival, "
          f"bot estimated {d.pps:+.3f}/share" for t, d, e in big]
    ev += [f"accepted tender {t.tid} {t.action} {t.ticker} x{t.qty} @ {d.price}: {100 * e:+.0f}c vs the mid on arrival "
           f"(bot estimated {d.pps:+.3f})" for t, d, e in bad]
    if not big and not bad:
        return Check("valuation", "Were profitable tenders declined?", clause, "ok",
                     "No declined tender was more than 10c through the mid on arrival.")
    edge_dollars = sum(e * t.qty for t, d, e in big)
    sizes = sorted({t.qty for t, _, _ in big})
    return Check("valuation", "Were profitable tenders declined?", clause, "gap",
                 f"{len(big)} declined tender(s) were 10c+ through the mid on arrival (gross edge "
                 f"{_fmt_money(edge_dollars)} before exit costs; sizes {', '.join(f'{s // 1000}k' for s in sizes)}). "
                 f"The bot walks a 2x book for the whole block, a cost its passive/hold unwind rarely pays. "
                 f"{len(close)} more were marginal declines. "
                 + (f"{len(bad)} accepted tender(s) had negative edge on arrival (the price moved our way before "
                    f"the late answer)." if bad else ""),
                 ev[:14] + ([f"... {len(ev) - 14} more"] if len(ev) > 14 else []),
                 ["refill-3", "consistent-valuation"], value=edge_dollars)


def robustness(run: Run, local: bool) -> Check:
    clause = "Data retrieval via RTD and order submission via the RIT API are both enabled."
    errs = run.api_errors
    if not errs and not run.crashed:
        slow = len(run.slow_loops)
        return Check("robustness", "Did the connection hold?", clause, "ok" if not slow else "info",
                     "No API errors." + (f" {slow} slow loops (> 500 ms)." if slow else ""))
    sim_end = local and all("Connection refused" in m for _, m in errs)
    c = run.clock()
    tick_first_err = run.tick_at(errs[0][0]) if errs and c else None
    when = ("after the bell" if tick_first_err is not None and tick_first_err >= 415 else
            f"from tick ~{tick_first_err}" if tick_first_err is not None else "at the end of the log")
    if sim_end:
        return Check("robustness", "Did the connection hold?", clause, "info",
                     f"{len(errs)} API errors {when}: the local simulator exits as soon as the case "
                     "ends, so the bot never saw STOPPED" + (" and gave up after 50 failed loops." if run.crashed else
                                                            " (it was stopped by hand).")
                     + " Not a bot gap: the real RIT client stays up between heats.",
                     [m[:160] for _, m in errs[:2]])
    return Check("robustness", "Did the connection hold?", clause, "gap",
                 f"{len(errs)} API errors" + (" and the bot crashed" if run.crashed else "") + ".",
                 [m[:160] for _, m in errs[:4]])


def interleaved(run: Run) -> Check | None:
    if run.bots <= 1:
        return None
    return Check("bots", "How many bots wrote this log?", "", "info",
                 f"{run.bots} bots started in the same second and wrote to this one file (parallel dry runs). Their "
                 "decisions are kept apart by tender id, side, stock and size; the market variant of each decision is "
                 "read off its tender price where it can be.")


def run_checks(run: Run, streams: list[tuple], gt: dict, local: bool) -> list[Check]:
    out = [interleaved(run), coverage(run, streams), answered(run), valuation(run, gt), auctions(run, gt),
           front_running(run, gt), overshoot(run, gt), robustness(run, local)]
    return [c for c in out if c is not None]


def valuation_from_ledger(ledger: list[dict]) -> Check:
    """Objective 1 on the merged ledger: each decision's edge is measured in the market variant that bot traded."""
    clause = ("Objective 1: evaluate profitability from the limit order book; accept tenders expected to make money, "
              "reject unattractive ones.")
    title = "Were profitable tenders declined?"
    big, bad, close, n = [], [], 0, 0
    for r in ledger:
        for d in r["decisions"]:
            if d["price"] is None or d["edge"] is None:
                continue
            n += 1
            e = d["edge"]
            if not d["accept"] and e > 0.10:
                big.append((r, d))
            elif not d["accept"] and e > 0 and (d["pps"] or -1) > -0.02:
                close += 1
            elif d["accept"] and e < 0:
                bad.append((r, d))
    if not n:
        return Check("valuation", title, clause, "info", "No fixed-price decisions with ground truth.")
    if not big and not bad:
        return Check("valuation", title, clause, "ok",
                     "No declined tender was more than 10c through the mid on arrival.")
    edge = sum(d["edge"] * r["qty"] for r, d in big)
    sizes = sorted({r["qty"] for r, _ in big})
    ev = [f"declined {r['tid']} {r['action']} {r['ticker']} x{r['qty']} @ {d['price']}: "
          f"{100 * d['edge']:+.0f}c vs the mid "
          f"on arrival, bot estimate {d['pps']:+.3f}/share" + (f" [{d['variant']}]" if d.get("variant") else "")
          for r, d in big]
    ev += [f"accepted {r['tid']} {r['action']} {r['ticker']} x{r['qty']} @ {d['price']}: "
           f"{100 * d['edge']:+.0f}c vs the mid "
           f"on arrival (estimate {d['pps']:+.3f})" for r, d in bad]
    return Check("valuation", title, clause, "gap",
                 f"{len(big)} declined decision(s) were 10c+ through the mid on arrival (gross edge {_fmt_money(edge)} "
                 f"before exit costs; sizes {', '.join(f'{x // 1000}k' for x in sizes)}). The bot walks a 2x book for "
                 f"the whole block, a cost its resting/hold unwind rarely pays. {close} more were marginal declines."
                 + (f" {len(bad)} accepted decision(s) had negative edge on arrival (the price moved our way "
                    f"before the "
                    f"late answer)." if bad else ""),
                 ev[:14] + ([f"... {len(ev) - 14} more"] if len(ev) > 14 else []),
                 ["refill-3", "consistent-valuation"], value=edge)


# ------------------------------------------------------------------------------------- A/B evidence (snapshot)
# Paired simulator A/B of candidate changes on the CURRENT bot, measured for this review (2026-10-10).
# Same seeds = same market for every setting; "hostile" = manipulators + crowded tenders, priced off the visible
# mid (anchor=1); "crowd at expiry" = other desks unwind the same blocks when each window closes.
AB_RESULTS = [
    {"change": "refill_factor 2 -> 3 and min_profit 0.01 -> 0", "seeds": "1-32 / holdout 101-132",
     "base": "+$4.5k (t 3.40) / +$3.6k (t 2.99), worst unchanged", "hostile": "+$0.3k (t 0.55) / +$1.3k (t 1.97)",
     "expiry": "-$3.5k (t -1.93), worst -$27k -> -$53k / -$1.4k (t -0.72), worst -$57.9k -> -$58.6k",
     "other": "thin books +$7.1k (t 4.91); tenders 10c worse +$5.5k (t 3.67); vol x2 +$0.3k; "
              "real-world + hostile +$0.2k",
     "verdict": "best candidate: passes base twice, never loses in hostile; adopt if practice shows no crowd "
                "at expiry"},
    {"change": "refill_factor 2 -> 3", "seeds": "1-32 / holdout 101-132",
     "base": "+$3.7k (t 2.83) / +$2.1k (t 2.12)", "hostile": "-$0.3k (t -0.91) / +$0.1k (t 1.17)",
     "expiry": "-$1.5k (t -1.24), worst -$27k -> -$44k / -$2.1k (t -1.14)",
     "other": "thin books +$6.0k (t 4.32); tenders 10c worse +$3.8k (t 2.79); vol x2 -$0.8k; "
              "real-world + hostile -$0.2k",
     "verdict": "weaker than the pair"},
    {"change": "min_profit 0.01 -> 0", "seeds": "1-32 / holdout 101-132",
     "base": "+$1.4k (t 1.81) / +$2.6k (t 2.80)", "hostile": "+$0.6k (t 1.31) / +$1.0k (t 1.76)",
     "expiry": "-$1.4k (t -1.07), worst -$27k -> -$53k / +$0.1k (t 0.06)",
     "other": "tenders 10c worse +$2.2k (t 2.10); thin books +$1.1k; real-world + hostile +$0.6k",
     "verdict": "fails the design-seed t >= 2 rule (t 1.81)"},
    {"change": "Value the held part of private tenders at the mid", "seeds": "1-16",
     "base": "+$3.5k (t 1.56)", "hostile": "+$2.3k (t 1.82)", "expiry": "-$9.3k (t -2.69), worst -$27k -> -$55k",
     "other": "", "verdict": "conditional on practice evidence (no crowd on private tenders)"},
    {"change": "Value every tender (incl. auctions) at the mid", "seeds": "1-16",
     "base": "+$2.1k (t 0.98)", "hostile": "+$1.7k (t 1.36)", "expiry": "-$13.2k (t -3.29)", "other": "",
     "verdict": "rejected"},
    {"change": "Bid auctions off the mid", "seeds": "1-16",
     "base": "-$1.2k (t -1.36)", "hostile": "-$0.5k (t -2.86)", "expiry": "-$3.7k (t -1.54)", "other": "",
     "verdict": "rejected"},
    {"change": "Value at the mid for the whole heat (hold window 420, valuation only)", "seeds": "1-16",
     "base": "+$2.0k (t 1.41)", "hostile": "$0", "expiry": "-$1.8k (t -0.77)", "other": "",
     "verdict": "not significant"},
    {"change": "Keep answering tenders in the last 5 s", "seeds": "1-32 / fresh 33-96",
     "base": "+$0.4k (t 1.53) / +$0.9k (t 3.14), worst unchanged", "hostile": "$0 / +$0.1k (t 1.05)",
     "expiry": "+$0.4k (t 1.52)", "other": "scarce resting fills +$0.4k; resting fills pay fees +$0.4k; it matters "
     "in about 1 heat in 6 and never made the worst heat worse", "verdict": "adopt (passes on 64 fresh seeds)"},
    {"change": "Rest the unwind one tick behind the touch", "seeds": "1-32",
     "base": "+$0.2k (t 0.12), worst +$20.0k -> -$4.9k", "hostile": "+$1.2k (t 1.48)", "expiry": "+$2.7k (t 1.08)",
     "other": "scarce fills +$0.1k; fees on resting fills +$1.0k", "verdict": "rejected (tail)"},
    {"change": "Never step inside the spread when resting", "seeds": "1-32",
     "base": "+$0.5k (t 0.52), worst +$20.0k -> +$5.6k", "hostile": "+$0.3k (t 0.50)", "expiry": "+$0.1k",
     "other": "scarce fills -$1.0k", "verdict": "rejected"},
    {"change": "Answer-deadline safety net (answer now if the next loop would miss)", "seeds": "33-96 / 1-16",
     "base": "normal speed: identical on 64 seeds", "hostile": "normal speed: identical on 64 seeds", "expiry": "",
     "other": "3 ticks per loop: identical; 4: +$9.8k (answered 76% -> 99%); 5: +$17.8k (57% -> 95%). Slow loops "
              "alone cost -$1.6k (2 ticks/loop), -$9.5k (3), -$22.2k (4), -$37.9k (5)",
     "verdict": "adopt (free insurance)"},
]


def execution_check(run: Run, rep: dict | None) -> Check:
    clause = ("Objective 2: use limit, market and marketable limit orders to mitigate liquidity and price risk "
              "from open positions.")
    title = "How were positions worked out?"
    ev = []
    if run.tca:
        tot = sum(r["total"] for r in run.tca)
        ev += [f"logged TCA {r['ticker']} {r['style']}: {r['filled']:,} shares, "
               f"{'earned' if r['per_unit'] < 0 else 'paid'} {abs(100 * r['per_unit']):.1f}c/share vs the arrival mid "
               f"({_fmt_money(-r['total'])})" for r in run.tca]
        ev.append(f"logged TCA total: {_fmt_money(-tot)} vs the arrival mids (positive = earned)")
    if not rep:
        if not run.tca:
            return Check("execution", title, clause, "info", "No fills in this log (dry run or killed before the TCA "
                         "report); see the replay of the current bot for execution numbers.")
        return Check("execution", title, clause, "info", "Execution from the bot's own TCA table.", ev)
    ex = rep["execution"]
    maker = sum(e["maker_sh"] for e in ex.values())
    taker = sum(e["taker_sh"] for e in ex.values())
    adverse = sum(e["adverse_sh"] for e in ex.values())
    cap = sum(e["maker_capture"] for e in ex.values())
    fees = sum(e["fees"] for e in ex.values())
    d = rep["decomposition"]["total"]
    share = maker / (maker + taker) if maker + taker else 0.0
    detail = (f"Current bot on this market: {share:.0%} of exits by resting orders ({maker:,} shares), {taker:,} by "
              f"crossing; commissions {_fmt_money(-fees)}; own impact {_fmt_money(d['impact'])}. Resting fills netted "
              f"{_fmt_money(cap)} vs the mid: {adverse:,} of {maker:,} resting shares filled when the price ran "
              "through the order (adverse selection), which eats the spread the resting orders earn.")
    status = "gap" if cap < 0 else "ok"
    return Check("execution", title, clause, status, detail, ev, ["passive-placement"] if cap < 0 else [],
                 value=cap if cap < 0 else None)


def closeout_check(rep: dict | None) -> Check:
    clause = ("Open positions are closed at the last traded price; markets can move against you, so large open "
              "positions carry meaningful downside risk.")
    title = "How much was held to the bell?"
    if not rep:
        return Check("closeout", title, clause, "info", "No replay (live run).")
    inv = rep["inventory"]
    d = rep["decomposition"]["total"]
    end = ", ".join(f"{t} {q:+,}" for t, q in inv["end_pos"].items() if q) or "flat"
    detail = (f"Current bot ends {end} ({inv['end_gross']:,} shares); close-out vs mid {_fmt_money(d['closeout'])}. "
              f"Peak gross {inv['peak_gross']:,} shares; peak 1-sd $ risk to the bell {_fmt_money(inv['peak_risk'])}, "
              f"average {_fmt_money(inv['avg_risk'])}. Holding to the bell is deliberate (no end-of-case fine in the "
              "2027 brief) but rests on that reading: confirm it in the first live practice heat "
              "(docs/ASSUMPTIONS.md A15).")
    return Check("closeout", title, clause, "info", detail)


def limits_check(run: Run, rep: dict | None) -> Check:
    clause = "Gross 250,000 / net 100,000 shares across all stocks; limits are enforced."
    title = "Did the limits cost tenders?"
    room = [(t, d) for t in run.tender_list() for d in t.decisions if "risk room" in d.reason]
    refused = [m for _, m in run.tender_fail if "LIMIT" in m]
    ev = [f"tender {t.tid} {t.action} {t.ticker} x{t.qty}: {d.reason}" for t, d in room]
    ev += [m[:160] for m in refused]
    rep_room = [r for r in (rep or {}).get("tenders", []) if r.get("reason") and "risk room" in r["reason"]]
    if rep_room:
        ev += [f"current bot: tender {r['tid']} {r['action']} {r['ticker']} x{r['qty']} declined, {r['reason']}"
               for r in rep_room]
    if not ev:
        return Check("limits", title, clause, "ok", "No tender was declined or refused for limit room.")
    return Check("limits", title, clause, "info",
                 f"{len(room)} logged decision(s) declined for limit room, {len(refused)} refused by the server"
                 + (f"; the current bot declined {len(rep_room)} on this market" if rep_room else "") + ".", ev)


def windows_check(rep: dict | None) -> Check:
    clause = ("Trades executed during a tender's decision window are flagged as front-running; only trades that "
              "reduce an existing position from an accepted tender are permitted without penalty.")
    title = "Did the current bot trade inside an open window?"
    if not rep:
        return Check("windows", title, clause, "info", "No replay (live run).")
    wf = rep.get("window_fills") or []
    if not wf:
        return Check("windows", title, clause, "ok", "The current bot traded nothing while a tender on that stock was "
                     "undecided.")
    sh = sum(w["qty"] for w in wf)
    race = [w for w in wf if w["arrival_race"]]
    other = [w for w in wf if not w["arrival_race"]]
    strict = rep["fines"]["strict"]
    ev = [f"tick {w['tick']}: {'resting' if w['maker'] else 'crossing'} {w['action']} {w['qty']:,} {w['ticker']}, "
          f"position {w['pos_before']:+,} -> {w['pos_after']:+,}"
          + (f"; tender {', '.join(map(str, w['tenders']))} arrived that tick" if w["arrival_race"] else "")
          for w in wf]
    detail = (f"{sh:,} shares in {len(wf)} fills, {len(race)} of them resting unwind orders filled in the very tick a "
              f"new tender on that stock arrived (before the bot could see it and pull them)"
              + (f", {len(other)} later" if other else "") + ". "
              + ("All reduce the position, which the brief permits: fine-free on that reading. "
                 if all(w["reduces"] for w in wf) else "")
              + f"On the strict reading (any trade in the window) the fine would be {_fmt_money(strict)}: "
              "check the first live practice heat's Transaction Log.")
    return Check("windows", title, clause, "gap" if other else "info", detail, ev, [], value=-strict)
