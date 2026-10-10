"""Post-trade gap reports (`ritc report`): log parsing, local/live classification, seed matching, P&L maths."""

import pytest

from ritc.postmortem import classify
from ritc.postmortem.findings import overshoot
from ritc.postmortem.logparse import parse_lines
from ritc.postmortem.replay import decompose, fine_schedule, replay, tender_table, window_fills
from ritc.postmortem.simmatch import find_seeds, stream

V1 = """\
22:46:56.242 INFO  ritc.bot | strategy=liability dry_run=False
22:46:58.243 INFO  ritc.liability | TENDER 3 BUY TAME x30000 @ 25.51 -> ACCEPT (profit/share +0.0980 >= 0.0100)
22:46:58.409 INFO  ritc.liability | TENDER 4 SELL CRZY x50000 @ 10.48 -> decline (profit/share -0.2752 < 0.0100)
22:46:58.410 INFO  ritc.algo | BLOCK start TAME +30000 -> +0 by tick 157
22:46:58.412 INFO  ritc.exec | [LIVE] SELL TAME 5000 @ 25.67
22:47:01.725 INFO  ritc.liability | TENDER 22 SELL CRZY x50000 @ None -> ACCEPT (competitive bid 10.40, profit/share +0.0500)
22:47:01.726 INFO  ritc.liability | TENDER 22 not filled (competitive bid 10.4 rejected)
22:47:02.562 INFO  ritc.algo | BLOCK start CRZY -30000 -> +0 by tick 197 AC kappa*T 0.33
22:47:05.270 INFO  ritc.algo | BLOCK start CRZY +2400 -> +0 by tick 223 AC kappa*T 0.30
"""

V3 = """\
10:53:13.628 INFO  ritc.bot | strategy=liability dry_run=False
10:53:21.713 INFO  ritc.liability | TENDER 1 SELL CROC x20000 @ None seen at tick 17, expires 41: answering 3 ticks before it
10:53:32.304 INFO  ritc.liability | TENDER 1 SELL CROC x20000 @ None -> ACCEPT (competitive bid 20.17, profit/share +0.0500)
10:53:43.470 INFO  ritc.liability | VOL CRZY $0.0213/tick  TAME $0.0209/tick  CROC $0.0421/tick
10:53:43.935 INFO  ritc.liability | TENDER 21 SELL CRZY x50000 @ None seen at tick 61, expires 82: answering 3 ticks before it
10:53:53.071 INFO  ritc.liability | TENDER 21 SELL CRZY x50000 @ None -> ACCEPT (competitive bid 9.87, profit/share +0.0500)
10:53:53.072 INFO  ritc.liability | TENDER 21 not filled (competitive bid 9.87 rejected)
10:54:03.488 INFO  ritc.bot | loop p50 7 ms, p95 38 ms, max 108 ms, feed 1 ms
"""


def test_parses_v1_and_v3_formats():
    r = parse_lines(V1.splitlines(), "logs/liability-20261009-224656.log")
    assert r.stamp == "20261009-224656" and r.version == "v1-instant" and r.bots == 1 and r.dry_run is False
    tl = r.tender_list()
    assert [t.tid for t in tl] == [3, 4, 22]
    assert tl[0].decisions[0].accept and tl[0].decisions[0].pps == pytest.approx(0.098)
    assert not tl[2].fixed and tl[2].decisions[0].bid == 10.40 and tl[2].rejected == [(tl[2].rejected[0][0], 10.4)]
    assert len(r.orders) == 1 and len(r.blocks) == 3
    r3 = parse_lines(V3.splitlines(), "x.log")
    assert r3.version == "v3-late-guard" and r3.loop_stats == [(7, 38, 108)]
    t1 = r3.tender_list()[0]
    assert (t1.seen_tick, t1.expires) == (17, 41)
    c = r3.clock()                                 # fitted from the `seen at tick` lines: ~2 ticks per second
    assert c is not None and 1.5 < c[1] < 5


def test_two_bots_in_one_file_are_kept_apart():
    lines = ["10:04:06.715 INFO  ritc.bot | strategy=liability dry_run=True"] * 2 + [
        "10:04:06.980 INFO  ritc.liability | TENDER 1 BUY TAME x10000 @ 24.92 -> ACCEPT (profit/share +0.1300 >= 0.0100)",
        "10:04:06.981 INFO  ritc.liability | TENDER 1 BUY TAME x20000 @ 24.84 -> decline (profit/share -0.0100 < 0.0100)"]
    r = parse_lines(lines, "x.log")
    assert r.bots == 2 and len(r.tenders) == 2      # same id, different tender: two keys


def test_overshoot_through_zero_is_flagged_but_a_rejected_bid_is_no_new_tender():
    r = parse_lines(V1.splitlines(), "x.log")
    c = overshoot(r)
    assert c.status == "fixed" and "2,400" in c.detail


def test_seed_is_found_from_a_dry_run_and_the_run_classified_local():
    tenders = stream(9)["tenders"][:8]
    lines = ["12:00:00.000 INFO  ritc.bot | strategy=liability dry_run=True"]
    for i, t in enumerate(tenders):
        px = f"{t['price']}" if t["fixed"] else "None"
        lines.append(f"12:00:{i + 1:02d}.000 INFO  ritc.liability | TENDER {t['index']} {t['action']} {t['ticker']} "
                     f"x{t['qty']} @ {px} -> decline (profit/share -0.0500 < 0.0100)")
    r = parse_lines(lines, "liability-20261012-120000.log")
    seeds = find_seeds(r, seeds=range(0, 20))
    assert [s["seed"] for s in seeds] == [9]
    assert classify(r, seeds)[0] == "local"


def test_an_unmatched_run_at_one_tick_per_second_is_live():
    lines = ["09:00:00.000 INFO  ritc.bot | strategy=liability dry_run=False",
             "09:00:01.000 INFO  ritc.liability | TENDER 5 BUY XYZ x10000 @ 9.9 seen at tick 10, expires 40: answering 3 ticks before it",
             "09:00:11.000 INFO  ritc.liability | TENDER 6 SELL XYZ x10000 @ 10.1 seen at tick 20, expires 50: answering 3 ticks before it",
             "09:00:21.000 INFO  ritc.liability | TENDER 7 BUY XYZ x20000 @ 9.8 seen at tick 30, expires 60: answering 3 ticks before it"]
    r = parse_lines(lines, "liability-20261012-090000.log")
    assert classify(r, [])[0] == "live"


def test_fine_schedule_matches_the_brief():
    assert fine_schedule(0) == 0
    assert fine_schedule(5000) == pytest.approx(1000.0)
    assert fine_schedule(6000) == pytest.approx(1400.0)        # $0.20 x 5,000 + $0.40 x 1,000


def test_replay_decomposition_adds_up_to_the_score():
    r = replay(5, keep_log=False)
    d = decompose(r)
    assert abs(d["check"]) < 1e-6
    rows = tender_table(r)
    assert rows and all("kind" in x for x in rows)
    assert any(x["answer"] for x in rows)
    # The only trades inside an undecided window are resting unwinds filled in the tick a tender arrives.
    wf = window_fills(r)
    assert wf and all(w["arrival_race"] and w["reduces"] and w["maker"] for w in wf)


def test_run_journal_records_a_heat_and_the_live_analysis_reads_it(tmp_path):
    import logging

    from ritc.cli import build
    from ritc.core.journal import Journal, read
    from ritc.postmortem.live import analyse, learnings
    from ritc.sim.server import InProcessAdapter, Market

    logging.disable(logging.CRITICAL)
    try:
        m = Market("liability", 420, 1, 5)
        m.status = "ACTIVE"
        runner, _ = build("liability", None, live=True)
        runner.client.adapter = InProcessAdapter(m)
        runner.interval = 0.0
        runner.on_loop = lambda: m.advance() if runner.loops % 4 == 0 else None
        path = tmp_path / "liability-20261012-120000.jsonl"
        runner.journal = runner.s.ex.journal = Journal(path)
        runner.run()
    finally:
        logging.disable(logging.NOTSET)
    kinds = {e["k"] for e in read(path)}
    assert {"start", "tick", "order", "tender_seen", "tender_decision", "tender_answer", "end"} <= kinds
    j = analyse(path)
    assert j["final_pnl"] == pytest.approx(m.nlv(), abs=1.0)
    assert len(j["tenders"]) >= 20 and any(r["booked_tick"] for r in j["tenders"])
    one = learnings("20261012-120000", "local", j)
    assert one["tenders"]["booked"] > 0 and isinstance(one["struggles"], dict)
