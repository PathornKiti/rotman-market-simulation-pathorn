"""
Stress report: the current bot through every `ritc stress` scenario, every seed, in ONE file of each kind.

    python -m ritc report --stress                     # 23 scenarios x seeds 1-16
    python -m ritc report --stress --seeds 32 --first-seed 401 --only core

Writes `reports/stress-<date>-<time>_local.html`, `.xlsx` and `.json`. The page opens with a plain-language
explanation of the case, then a table of every scenario; each scenario section has its gap checks, every seed
side by side (vs the base market and the official template), and per seed a "what happened" summary and the
bot's full log. The workbook has one sheet per kind of data (Scenarios with formulas over Seeds, every tender,
the help file's Tender replay layout); the JSON holds everything, logs included, for a later review.
"""

from __future__ import annotations

import re
import statistics as st
import time
from concurrent.futures import ProcessPoolExecutor

from .html import PART_LABEL, bars, cents, esc, money, pill, positions_chart
from .replay import PARTS, decompose, execution, fines, inventory, replay, tender_table, window_fills

BASE = "base (brief)"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _levels(book: dict | None, side: str, n: int = 10) -> list[tuple[float, int]]:
    agg: dict[float, int] = {}
    for o in (book or {}).get(side, []):
        if o.get("price") is None:
            continue
        agg[float(o["price"])] = agg.get(float(o["price"]), 0) + int(o.get("quantity", 0)) - int(
            o.get("quantity_filled", 0) or 0)
    return sorted(agg.items(), reverse=(side == "bid"))[:n]


def _run(args) -> dict:
    name, seed = args
    from ..stress import SCENARIOS
    stress, hostile, queue, loops, group = SCENARIOS[name]
    r = replay(seed, stress, hostile, queue=queue, loops_per_tick=loops, keep_log=True)
    from ..stress import run_template
    try:
        template = run_template(seed, name)["score"]
    except Exception:                       # noqa: BLE001 - the benchmark must not kill the report
        template = None
    rows = tender_table(r)
    books = {}
    for row in rows:
        tk = row["ticker"]
        b = row.pop("book_answer", None) or r["rec"]["tenders"][row["tid"]].get("book")
        books[row["tid"]] = {"tick": row["answer_tick"] or row["tick"], "ticker": tk,
                             "bid": _levels(b, "bid"), "ask": _levels(b, "ask")}
    inv = inventory(r)
    return {"scenario": name, "group": group, "seed": seed, "score": r["score"], "penalty": r["penalty"],
            "parts": decompose(r)["total"], "execution": execution(r), "fines": fines(r),
            "inventory": {k: v for k, v in inv.items() if k != "series"},
            "series": [{"tick": x["tick"], "pos": x["pos"], "nlv": x["nlv"]} for x in inv["series"]],
            "window_fills": window_fills(r), "tenders": rows, "books": books, "limit_rejects": r["limit_rejects"],
            "log": r["log"], "template": template}


def run_all(seeds: list[int], names: list[str], workers: int | None = None, progress=print) -> dict[str, list[dict]]:
    jobs = [(n, s) for n in names for s in seeds]
    progress(f"stress report: {len(names)} scenario(s) x {len(seeds)} seed(s) = {len(jobs)} heats")
    out: dict[str, list[dict]] = {n: [] for n in names}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, res in enumerate(pool.map(_run, jobs, chunksize=1), 1):
            out[res["scenario"]].append(res)
            if i % 50 == 0 or i == len(jobs):
                progress(f"  {i}/{len(jobs)} heats ({time.time() - t0:.0f}s)")
    for v in out.values():
        v.sort(key=lambda h: h["seed"])
    return out


# --------------------------------------------------------------------------------------------- aggregation
def summarise(heats: list[dict], base: list[dict] | None) -> dict:
    sc = sorted(h["score"] for h in heats)
    q = max(1, len(sc) // 4)
    by_seed = {h["seed"]: h["score"] for h in (base or [])}
    diffs = [h["score"] - by_seed[h["seed"]] for h in heats if h["seed"] in by_seed]
    t = None
    if len(diffs) > 1 and st.stdev(diffs) > 0:
        t = st.fmean(diffs) / (st.stdev(diffs) / len(diffs) ** 0.5)
    tenders = [r for h in heats for r in h["tenders"]]
    offered = [r for r in tenders if r["tick"] <= 414]
    booked = [r for r in tenders if r.get("booked_tick") is not None]
    bids = [r for r in tenders if r["bid"] is not None]
    short = [r for r in bids if r.get("bid_vs_reserve") is not None and r["bid_vs_reserve"] < 0]
    won = [r for r in bids if r.get("booked_tick") is not None]
    big = [r for r in tenders if r["kind"] == "private" and r["answer"] == "decline" and (r["edge_answer"] or 0) > 0.10]
    unanswered = [r for r in offered if r["answer"] is None]
    room = [r for r in tenders if r.get("reason") and "risk room" in r["reason"]]
    acc = [r for r in booked if r.get("pnl_ps") is not None]
    qty = sum(r["qty"] for r in acc) or 1
    n = len(heats)
    ex = [h["execution"] for h in heats]
    tot = lambda key: sum(e[t][key] for e in ex for t in e)            # noqa: E731
    return {
        "n": n, "mean": st.fmean(sc), "sd": st.pstdev(sc), "worst": sc[0], "best": sc[-1],
        "cvar25": st.fmean(sc[:q]), "losing": sum(x < 0 for x in sc),
        "vs_base": st.fmean(diffs) if diffs else None, "t": t,
        "parts": {k: st.fmean(h["parts"][k] for h in heats) for k in PARTS},
        "offered": len(offered) / n, "answered": (len(offered) - len(unanswered)) / n, "taken": len(booked) / n,
        "unanswered": len(unanswered), "bids": len(bids), "bids_won": len(won), "bids_short": len(short),
        "short_median": sorted(-r["bid_vs_reserve"] for r in short)[len(short) // 2] if short else None,
        "big_declines": len(big), "big_edge": sum(r["edge_answer"] * r["qty"] for r in big),
        "room": len(room), "est_ps": sum((r["pps_est"] or 0) * r["qty"] for r in acc) / qty,
        "real_ps": sum(r["pnl_ps"] * r["qty"] for r in acc) / qty,
        "lenient": st.fmean(h["fines"]["lenient"] for h in heats), "strict": st.fmean(h["fines"]["strict"] for h in heats),
        "strict_max": max(h["fines"]["strict"] for h in heats),
        "window_sh": sum(w["qty"] for h in heats for w in h["window_fills"]) / n,
        "window_races": all(w["arrival_race"] for h in heats for w in h["window_fills"]),
        "maker_sh": tot("maker_sh") / n, "taker_sh": tot("taker_sh") / n, "adverse_sh": tot("adverse_sh") / n,
        "maker_capture": tot("maker_capture") / n, "fees": tot("fees") / n,
        "held": st.fmean(h["inventory"]["end_gross"] for h in heats),
        "held_max": max(h["inventory"]["end_gross"] for h in heats),
        "peak_gross": max(h["inventory"]["peak_gross"] for h in heats),
        "peak_risk": st.fmean(h["inventory"]["peak_risk"] for h in heats),
        "limit_rejects": sum(h["limit_rejects"] for h in heats),
        "template_mean": st.fmean(t) if (t := [h["template"] for h in heats if h["template"] is not None]) else None,
        "template_losing": sum(1 for h in heats if h["template"] is not None and h["template"] < 0),
    }


def checks(s: dict) -> list[dict]:
    """The log reports' gap checks, over every seed of the scenario."""
    n = s["n"]

    def c(cid, title, status, detail):
        return {"id": cid, "title": title, "status": status, "detail": detail}
    out = [
        c("losing", "Losing heats", "gap" if s["losing"] else "ok",
          f"{s['losing']} of {n} heats lost money; worst {money(s['worst'])}, CVaR25 {money(s['cvar25'])}."),
        c("answered", "Every tender answered?", "gap" if s["unanswered"] else "ok",
          f"{s['answered']:.1f} of {s['offered']:.1f} tenders a heat answered" + (
              f"; {s['unanswered']} unanswered over {n} heats." if s["unanswered"] else ".")),
        c("valuation", "Profitable tenders declined?", "gap" if s["big_declines"] else "ok",
          f"{s['big_declines'] / n:.1f} private tenders a heat declined while 10c+ inside the mid when answered "
          f"(gross edge {money(s['big_edge'] / n)} a heat). Accepted tenders: estimate {cents(s['est_ps'])}/share, "
          f"realized {cents(s['real_ps'])}/share."),
        c("auctions", "Auction bids", "gap" if s["bids_short"] else "ok",
          f"{s['bids']} bids, {s['bids_won']} won, {s['bids_short']} short of the hidden reserve"
          + (f" (median {100 * s['short_median']:.0f}c)" if s["short_median"] is not None else "") + "."),
        c("fines", "Fines", "gap" if s["lenient"] else ("info" if s["strict"] else "ok"),
          f"charged {money(s['lenient'])} a heat; on the strict front-running reading {money(s['strict'])} a heat "
          f"(max {money(s['strict_max'])}), from {s['window_sh']:,.0f} shares a heat traded in open windows"
          + (" - all resting fills in the tick a tender arrived." if s["window_sh"] and s["window_races"] else ".")),
        c("execution", "Execution (Objective 2)", "gap" if s["maker_capture"] < 0 else "ok",
          f"{s['maker_sh']:,.0f} shares a heat out by resting orders, {s['taker_sh']:,.0f} by crossing; resting fills "
          f"{money(s['maker_capture'], True)} a heat vs the mid ({s['adverse_sh']:,.0f} adverse); fees "
          f"{money(-s['fees'], True)}."),
        c("bell", "Held to the bell", "info",
          f"{s['held']:,.0f} shares a heat on average (max {s['held_max']:,}); peak gross {s['peak_gross']:,}; "
          f"peak 1-sd risk to the bell {money(s['peak_risk'])} a heat."),
        c("limits", "Limits", "info" if s["room"] or s["limit_rejects"] else "ok",
          f"{s['room']} tenders declined for limit room; {s['limit_rejects']} orders/tenders refused by the server."),
    ]
    return out


# --------------------------------------------------------------------------------------------- html
def _seed_table(sid: str, heats: list[dict], base: list[dict] | None) -> str:
    by = {h["seed"]: h["score"] for h in (base or [])}
    head = ("<tr><th class=n>Seed</th><th class=n>Score</th><th class=n>vs base</th>"
            + "".join(f"<th class=n>{esc(PART_LABEL[k])}</th>" for k in PARTS)
            + "<th class=n>Taken / offered</th><th class=n>Bids won</th><th class=n>Strict fine</th>"
              "<th class=n>Held at bell</th><th class=n>Template</th></tr>")
    rows = []
    worst = min(h["score"] for h in heats)
    for h in heats:
        off = sum(1 for r in h["tenders"] if r["tick"] <= 414)
        tk = sum(1 for r in h["tenders"] if r.get("booked_tick") is not None)
        bids = [r for r in h["tenders"] if r["bid"] is not None]
        won = sum(1 for r in bids if r.get("booked_tick") is not None)
        vb = money(h["score"] - by[h["seed"]], True) if h["seed"] in by else ""
        rows.append(f'<tr class="{"hl" if h["score"] == worst else ""}"><td class=n><a href="#{sid}-seed-{h["seed"]}">'
                    f'{h["seed"]}</a></td><td class=n>{money(h["score"])}</td><td class=n>{vb}</td>'
                    + "".join(f"<td class=n>{money(h['parts'][k], True)}</td>" for k in PARTS)
                    + f"<td class=n>{tk} / {off}</td><td class=n>{won} / {len(bids)}</td>"
                      f"<td class=n>{money(h['fines']['strict'])}</td><td class=n>{h['inventory']['end_gross']:,}</td>"
                      f"<td class=n>{money(h['template']) if h['template'] is not None else ''}</td></tr>")
    return '<div class="card scroll"><table>' + head + "".join(rows) + "</table></div>"


def lessons(h: dict, base_score: float | None) -> list[str]:
    """What happened in one heat, in a few lines: where the money came from and what went wrong."""
    out = [f"Score {money(h['score'])}" + (f" ({money(h['score'] - base_score, True)} vs the base market)"
                                           if base_score is not None else "")
           + (f"; the official template made {money(h['template'])}" if h["template"] is not None else "") + "."]
    p = h["parts"]
    out.append(f"Tender edge {money(p['edge'], True)}, inventory moves {money(p['inventory'], True)}, resting fills "
               f"{money(p['spread_maker'], True)}, crossing {money(p['spread_taker'] + p['fees'] + p['impact'], True)}, "
               f"close-out {money(p['closeout'], True)}, fines {money(p['fines'], True)}.")
    booked = [r for r in h["tenders"] if r.get("booked_tick") is not None and r.get("pnl") is not None]
    if booked:
        best, worst = max(booked, key=lambda r: r["pnl"]), min(booked, key=lambda r: r["pnl"])
        out.append(f"{len(booked)} tenders taken; best #{best['tid']} {best['action']} {best['qty']:,} {best['ticker']} "
                   f"{money(best['pnl'], True)}, worst #{worst['tid']} {worst['action']} {worst['qty']:,} "
                   f"{worst['ticker']} {money(worst['pnl'], True)}.")
    big = [r for r in h["tenders"] if r["kind"] == "private" and r["answer"] == "decline" and (r["edge_answer"] or 0) > 0.10]
    if big:
        out.append("Declined while 10c+ inside the mid: " + ", ".join(
            f"#{r['tid']} {r['action']} {r['qty'] // 1000}k {r['ticker']} ({cents(r['edge_answer'])}, est {cents(r['pps_est'])})"
            for r in big) + ".")
    bids = [r for r in h["tenders"] if r["bid"] is not None]
    if bids:
        won = sum(1 for r in bids if r.get("booked_tick") is not None)
        short = [r for r in bids if r.get("bid_vs_reserve") is not None and r["bid_vs_reserve"] < 0]
        out.append(f"Auction bids: {won} of {len(bids)} won, {len(short)} short of the reserve.")
    un = [r for r in h["tenders"] if r["answer"] is None and r["tick"] <= 414]
    if un:
        out.append(f"{len(un)} tenders never answered: " + ", ".join(f"#{r['tid']}" for r in un) + ".")
    if h["window_fills"]:
        out.append(f"{sum(w['qty'] for w in h['window_fills']):,} shares filled inside an open tender window "
                   f"(strict-reading fine {money(h['fines']['strict'])}).")
    inv = h["inventory"]
    out.append(f"Peak gross {inv['peak_gross']:,} shares; held at the bell {inv['end_gross']:,}.")
    return out


def scenario_section(name: str, heats: list[dict], base: list[dict] | None, s: dict, knobs: tuple) -> str:
    """One scenario of the combined page: tiles, checks, every seed, and each heat's summary + bot log."""
    stress, hostile, queue, loops, group = knobs
    sid = "sc-" + slug(name)
    by = {b["seed"]: b["score"] for b in (base or [])}
    vs = money(s["vs_base"], True) if s["vs_base"] is not None else "-"
    vs_note = f"paired per seed, t {s['t']:.2f}" if s["t"] is not None else "same seeds"
    tiles = (f'<div class="tiles"><div class="tile"><div class="label">Mean</div><div class="value">{money(s["mean"])}</div>'
             f'<div class="note">sd {money(s["sd"])} · best {money(s["best"])}</div></div>'
             f'<div class="tile"><div class="label">Worst / CVaR25</div><div class="value">{money(s["worst"])}</div>'
             f'<div class="note">CVaR25 {money(s["cvar25"])} · losing {s["losing"]}/{s["n"]}</div></div>'
             f'<div class="tile"><div class="label">vs base market</div><div class="value">{vs}</div>'
             f'<div class="note">{vs_note}</div></div>'
             f'<div class="tile"><div class="label">Official template</div><div class="value">'
             f'{money(s["template_mean"]) if s["template_mean"] is not None else "-"}</div>'
             f'<div class="note">losing {s["template_losing"]}/{s["n"]}</div></div></div>')
    chk = "".join(f'<div class="check"><div>{pill(c["status"])}</div><div><div class="t">{esc(c["title"])}</div>'
                  f'<div>{esc(c["detail"])}</div></div></div>' for c in checks(s))
    logs = "".join(
        f'<details id="{sid}-seed-{h["seed"]}"><summary><b>Seed {h["seed"]}</b> · {money(h["score"])}</summary><ul>'
        + "".join(f"<li>{esc(x)}</li>" for x in lessons(h, by.get(h["seed"])))
        + f'</ul><pre class="log">{esc(chr(10).join(h.get("log") or []))}</pre></details>' for h in heats)
    worst = min(heats, key=lambda h: h["score"])
    return (f'<section id="{sid}"><details><summary><h2 style="display:inline">{esc(name)}</h2> '
            f'<span class="muted">{esc(group)} · {money(s["mean"])} mean · worst {money(s["worst"])} · losing '
            f'{s["losing"]}/{s["n"]}</span></summary>'
            f'<p class=sub>RITC_STRESS=<code>{esc(stress or "-")}</code> · hostile {hostile:g} · '
            f'{"queue model · " if queue else ""}{loops} bot loop(s) per tick · <a href="#top">all scenarios</a></p>'
            + tiles + f'<div class="card">{chk}</div>'
            + "<h3>Score by seed</h3>" + bars([(f"seed {h['seed']}", h["score"], "") for h in heats])
            + "<h3>Where the P&amp;L came from (mean)</h3>" + bars([(PART_LABEL[k], s["parts"][k], "") for k in PARTS])
            + "<h3>Every seed (click a seed for its log)</h3>" + _seed_table(sid, heats, base)
            + f"<h3>Worst heat: seed {worst['seed']}</h3>" + positions_chart(worst["series"])
            + "<h3>Each heat: what happened and the bot's log</h3>" + f'<div class="card">{logs}</div>'
            + "</details></section>")


def stress_block(results: dict, sums: dict, stamp: str, seeds: list[int]) -> str:
    """The stress test as one block of the combined report (id stress)."""
    rows = []
    for name, s in sums.items():
        vs = (money(s["vs_base"], True) + (f" (t {s['t']:.1f})" if s["t"] is not None else "")) if s["vs_base"] is not None else "-"
        rows.append(f'<tr class="{"hl" if s["losing"] else ""}"><td><a href="#sc-{slug(name)}">{esc(name)}</a></td>'
                    f'<td>{esc(results[name][0]["group"])}</td><td class=n>{money(s["mean"])}</td>'
                    f'<td class=n>{money(s["worst"])}</td><td class=n>{money(s["cvar25"])}</td>'
                    f'<td class=n>{s["losing"]}/{s["n"]}</td><td class=n>{vs}</td><td class=n>{s["taken"]:.1f}</td>'
                    f'<td class=n>{money(s["lenient"])} / {money(s["strict"])}</td><td class=n>{s["held"]:,.0f}</td>'
                    f'<td class=n>{money(s["template_mean"]) if s["template_mean"] is not None else "-"}</td>'
                    f'<td class=n>{s["template_losing"]}/{s["n"]}</td></tr>')
    from ..stress import SCENARIOS
    return (f'<h2 id="stress">Stress test (simulator, run {stamp})</h2><p>The current bot (config/liability.toml) through '
            f"every <code>ritc stress</code> scenario, seeds {seeds[0]}-{seeds[-1]}, the same seeds in every scenario. Click "
            "a scenario to open it; inside, click a seed for that heat's summary and the bot's full log.</p>"
            + bars([(k, v["mean"], f"worst {money(v['worst'])}") for k, v in sums.items()])
            + '<div class="card scroll"><table><tr><th>Scenario</th><th>Group</th><th class=n>Mean</th>'
            "<th class=n>Worst</th><th class=n>CVaR25</th><th class=n>Losing</th><th class=n>vs base</th>"
            "<th class=n>Taken/heat</th><th class=n>Fines (charged / strict)</th><th class=n>Held at bell</th>"
            "<th class=n>Template mean</th><th class=n>Template losing</th></tr>" + "".join(rows) + "</table></div>"
            '<p class="muted">Highlighted: at least one losing heat. vs base = paired mean difference to the brief\'s market '
            "on the same seeds. Every heat is an instrumented lock-step replay; the P&amp;L parts add up to the score.</p>"
            + "".join(scenario_section(n, results[n], results.get(BASE), sums[n], SCENARIOS[n]) for n in sums))


def combined_json(results: dict, sums: dict, stamp: str, seeds: list[int]) -> dict:
    """Everything a later review needs, machine-readable: summaries, checks, every heat with its lessons and log."""
    from ..stress import SCENARIOS
    base = {h["seed"]: h["score"] for h in results.get(BASE, [])}
    out = {"run": stamp, "mode": "local", "seeds": seeds, "scenarios": {}}
    for n, s in sums.items():
        stress, hostile, queue, loops, group = SCENARIOS[n]
        out["scenarios"][n] = {
            "knobs": {"stress": stress, "hostile": hostile, "queue": queue, "loops_per_tick": loops, "group": group},
            "summary": s, "checks": checks(s),
            "heats": [{"seed": h["seed"], "score": h["score"], "template": h["template"], "parts": h["parts"],
                       "fines": h["fines"], "inventory": h["inventory"], "lessons": lessons(h, base.get(h["seed"])),
                       "tenders": [{k: v for k, v in r.items() if k not in ("book",)} for r in h["tenders"]],
                       "log": h.get("log")} for h in results[n]]}
    return out


def add_stress_sheets(wb, results: dict, sums: dict) -> None:
    """`Scenarios` (formulas over `Seeds`) and `Seeds` (one row per heat; the parts add up to the score)."""
    from ..stress import SCENARIOS
    from .xlsx import BLUE, MONEY, _f, _header, _put
    sc, seeds_ws = wb.create_sheet("Scenarios"), wb.create_sheet("Seeds")
    pcols = [chr(ord("D") + i) for i in range(len(PARTS))]
    sum_col = chr(ord("D") + len(PARTS))
    chk_col = chr(ord(sum_col) + 1)
    _header(seeds_ws, 1, ["Scenario", "Seed", "Score"] + [PART_LABEL[k] for k in PARTS] + ["Sum of parts", "Check",
            "Template", "Taken", "Offered", "Strict fine", "Held at bell"])
    r = 2
    for n in sums:
        for h in results[n]:
            for j, v in enumerate([n, h["seed"], h["score"]] + [h["parts"][k] for k in PARTS], 1):
                c = seeds_ws.cell(row=r, column=j, value=v)
                c.font = _f(BLUE)
                if j > 2:
                    c.number_format = MONEY
            seeds_ws[f"{sum_col}{r}"] = f"=SUM({pcols[0]}{r}:{pcols[-1]}{r})"
            seeds_ws[f"{sum_col}{r}"].number_format = MONEY
            seeds_ws[f"{chk_col}{r}"] = f"=ROUND({sum_col}{r}-C{r},2)"
            ev = [h["template"], sum(1 for t in h["tenders"] if t.get("booked_tick") is not None),
                  sum(1 for t in h["tenders"] if t["tick"] <= 414), h["fines"]["strict"], h["inventory"]["end_gross"]]
            for j, v in enumerate(ev):
                c = seeds_ws.cell(row=r, column=ord(chk_col) - ord("A") + 2 + j, value=v)
                c.font = _f(BLUE)
                if j in (0, 3):
                    c.number_format = MONEY
            r += 1
    last = r - 1
    tcol, fcol = chr(ord(chk_col) + 1), chr(ord(chk_col) + 4)
    seeds_ws.freeze_panes = "C2"
    _header(sc, 1, ["Scenario", "Group", "RITC_STRESS", "Hostile", "Heats", "Mean", "Worst", "Best", "Losing heats",
                    "vs base (mean)", "Template mean", "Template losing", "Strict fine / heat"])
    rng = lambda col: f"Seeds!${col}$2:${col}${last}"            # noqa: E731
    for i, n in enumerate(sums, 2):
        stress, hostile, _q, _l, group = SCENARIOS[n]
        for j, v in enumerate([n, group, stress or "-", hostile], 1):
            sc.cell(row=i, column=j, value=v).font = _f(BLUE)
        f = {"E": f'=COUNTIF({rng("A")},A{i})', "F": f'=AVERAGEIF({rng("A")},A{i},{rng("C")})',
             "G": f'=_xlfn.MINIFS({rng("C")},{rng("A")},A{i})', "H": f'=_xlfn.MAXIFS({rng("C")},{rng("A")},A{i})',
             "I": f'=COUNTIFS({rng("A")},A{i},{rng("C")},"<0")', "J": f"=F{i}-$F$2",
             "K": f'=AVERAGEIF({rng("A")},A{i},{rng(tcol)})', "L": f'=COUNTIFS({rng("A")},A{i},{rng(tcol)},"<0")',
             "M": f'=AVERAGEIF({rng("A")},A{i},{rng(fcol)})'}
        for col, formula in f.items():
            sc[f"{col}{i}"] = formula
            sc[f"{col}{i}"].number_format = MONEY if col in "FGHJKM" else "#,##0"
    for c, w in zip("ABCDEFGHIJKLM", (26, 9, 40, 8, 7, 11, 11, 11, 8, 12, 12, 9, 11)):
        sc.column_dimensions[c].width = w
    sc.freeze_panes = "B2"
    _put(sc, f"A{len(sums) + 3}", "Row 2 is the base market: vs base = this scenario's mean minus it (same seeds).",
         italic=True)


def stress_tender_rows(results: dict, sums: dict) -> list[dict]:
    """Every tender of every heat in the shape of the combined workbook's `Tenders` sheet."""
    out = []
    for n in sums:
        for h in results[n]:
            for t in h["tenders"]:
                ans = "not answered" if t["answer"] is None else ("ACCEPT" if t["answer"] == "accept" else "decline")
                out.append({"tid": t["tid"], "kind": t["kind"], "action": t["action"], "ticker": t["ticker"],
                            "qty": t["qty"], "price": t["price"], "tick": t["tick"], "expires": t["expires"],
                            "answer_tick": t["answer_tick"], "answer": ans, "pps": t["pps_est"], "bid": t["bid"],
                            "mid_answer": t["mid_answer"], "mid": t["mid"], "reserve": t["reserve"],
                            "rival": t["rival"], "outcome": "booked" if t.get("booked_tick") is not None else (
                                "not won" if t["answer"] == "accept" else ""),
                            "book": h["books"][t["tid"]], "seed": h["seed"], "realized": t.get("pnl_ps"),
                            "source": f"stress: {n}"})
    return out


def run_stress(seeds: list[int], only: str | None = None, workers: int | None = None, progress=print) -> dict:
    """Run every (selected) scenario on every seed: {"results", "sums", "seeds", "stamp"}."""
    from ..stress import SCENARIOS
    wanted = [w.strip() for w in only.split(",")] if only else []
    names = [n for n, v in SCENARIOS.items() if not wanted or v[4] in wanted or n in wanted]
    names = [BASE] + [n for n in names if n != BASE]   # every scenario is compared with the base market
    results = run_all(seeds, names, workers, progress)
    sums = {n: summarise(results[n], results.get(BASE)) for n in names}
    return {"results": results, "sums": sums, "seeds": seeds, "stamp": time.strftime("%Y%m%d-%H%M%S")}
