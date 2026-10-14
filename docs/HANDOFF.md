# Handoff: 2026-10-14 late night, a731c81 batch reviewed (START HERE)

> **CASE RULE (organisers; keep this note in EVERY handoff):** The speculation fine will increase from $0.40 to
> $0.60 per share for speculation exceeding 5,000 shares. The fine for speculation of 5,000 shares or fewer remains
> unchanged at $0.20 per share.

**Batch:** 3 heats 22:15-22:32, ~3 other teams online, run with the COMMITTED a731c81 config (edge floor AND
soft_trend on; the `--set soft_trend=false` override was not used). NLV $32.2k, -$44.7k, $3.8k. The b19e95c evening
heats (12, 19:42-21:11) averaged $37k, worst $19k. Logs: logs/ + ~/Downloads/log_transfer (14 earlier evening journals).

**Why it was worse: CROC only.** The replay (`docs/results/journal_replay.py`, old vs as-run config) differs on exactly 2
tenders, both from the edge floor: 22:23 #402 BUY CROC 54k (cost model -0.25/sh) made -$51.8k; 22:32 #302 SELL CROC 54k
(model -0.36, stacked on a -33k block) about -$28.6k. The soft trend then unwound them at 684-874 sh/s while the mid ran
-1.17 / -0.71. CROC per heat: b19e95c evening +$8.2k mean, this batch -$22.4k. TAME+CRZY: $29.0k -> $19.4k (3 heats,
t about -1, no code change there).
**Done (uncommitted):** `[strategy.per_ticker.CROC] edge_floor = 0.0` (soft_trend was already false) = b19e95c on
CROC. Replay of 22:23 now declines #402. 182 tests pass.
VERIFIED: replaying 7 heats (19:42-19:58, 21:11, 22:15-22:32) through b19e95c and through the working tree gives
identical TENDER / SPRINT / BLOCK / CROWD lines. Windows without a commit: `--set strategy.per_ticker.CROC.edge_floor=0
--set strategy.per_ticker.CROC.soft_trend=false` gives the same.

**Main pain point of evening heats (user question): a crowded exit.** 17 evening journals, 55 blocks: tender edge
+$1,000k, mid move against us during the unwind -$501k, fills -$26k, made +$468k (CROC kept 10% of its edge, TAME 56%,
CRZY 80%). Within 20 ticks of a fixed tender the mid runs against its holder by 0.11 + 0.59 x the 10-tick trend before it +
0.23 x the tender edge (t 3.2 / 3.4 / 2.1, n 118). Whether WE took it: -0.02 (t -0.3), so this is not our own impact.
Lost auctions (one winner) show none of it (+0.002 at +10 ticks). Day heats: +0.03..0.06 at +10 vs evening +0.15. So the
other teams take the same fixed tender and sell into the same book. Scripts: `docs/results/evening_2026-10-14/`.
What does NOT fix it (real-data checks): (a) racing at block start on the recorded books: CRZY -$36k..-49k (t -3..-4),
TAME ~0, CROC + but n.s. (optimistic refills); (b) a bigger valuation charge: the cost model already sorts outcomes
(blocks valued < +0.10: 14, -$34k, 7 losers; >= +0.10: 40, all buckets profitable); (c) the CROC edge floor and soft
trend. Crowd detector: "no crowd detected" in all 3 heats (it pools lost auctions with ~0 move and scores only 10 ticks).
Fixing it would switch on the crowd charge and race, and both look harmful above, so leave it.
**PATIENT TAIL: built, checked on real data, REJECTED, code removed (note in config [execution]).** After a mostly-rested
block ends, the mid does come back our way (+0.21 in 20 ticks, t 8.2, n 29), but only AFTER the block. Replaying the 17
evening journals with the bundle's `ride` (rest 1 tick behind the touch when momentum says "our way"): the mid then
moved AGAINST us -0.042 in 5 ticks (t -2.6); time & sales fill estimate +$0.4k/heat. Riding whenever resting: -0.125
(t -4.9), -$1.6k/heat. Only the last 25% of a block: -$0.1k/heat.

**EARLY TENDERS (user question: why nothing early?).** The schedule: fixed tenders at ticks ~15-30 (#2), ~76-89 (#102),
~135-148, then every ~60; auctions 0 of 68 won. So before tick 100 there are only 2 chances. 17 evening heats: the bot
took #2 in 11/17 and something before tick 100 in 13/17; nothing until tick 136-206 in 4/17. Early tenders it took made
+0.160/share (17). Early declined (17): model value -0.318 (charges: drift 0.245, book walk 0.154 (book mode: adaptive
gate needs 2 scored tenders), risk 0.042, crowd 0.044). The recorded price path says about +0.06/share, and that path
overstates taken blocks by 0.054, so they were about break-even (swings -0.56..+0.54). The model's LEVEL is too
pessimistic but its RANKING is right, so a "less pessimistic early" fix (bundle `early_probe_ticks`, drift cap) mostly
adds break-even, high-variance tenders. Not a priority. Scripts: `docs/results/evening_2026-10-14/`.

**AUCTIONS (user question: why always lost?).** 17 evening heats: 68 auctions (39 Above_Reserve, 29 Winner_Take_ALL),
bid on 67, won 0 (solo heats 2026-10-13: 0/26). Our bid sits a median 0.19 (reserve) / 0.23 (WTA) from the listing mid
(= the fixed-tender charges + 0.05 margin); we lost even at 0.075-0.105 (8 auctions), so the winning price (hidden
reserve, or a rival for WTA) is inside ~0.075. A block won at distance x from the mid, on the recorded path (awarded at
expiry, minus the 0.054 path overstatement): x = 0 -0.145/sh, 0.05 -0.095, 0.10 -0.045, 0.15 +0.005, 0.20 +0.055. So any
price that wins loses money (~-$3k per 55k block), and the blocks also take net-limit room from fixed tenders. Losing
is correct. Only "trend already our way" auctions look positive (+0.15/sh at 0.03 from the mid at threshold 0.02), but
it is not robust (t 1.1 / 2.2 / 1.1 at thresholds 0 / 0.02 / 0.05; TAME negative): do not build. `auctions*.py`.
Correction to wording above: per the brief, fixed tenders are PRIVATE (they differ by team in timing, price and size);
the evening move is rivals unwinding SIMILAR blocks at the same time. Auctions go to every team at once.

---

# Handoff: 2026-10-14 night, per-ticker version (history)

> **CASE RULE (organisers; keep this note in EVERY handoff):** The speculation fine will increase from $0.40 to
> $0.60 per share for speculation exceeding 5,000 shares. The fine for speculation of 5,000 shares or fewer remains
> unchanged at $0.20 per share. (Code: sim `_apply_fill`, `postmortem/replay.py` + `live.py` FINE1/FINE2, Excel
> template: all $0.20 / $0.60.)

**Base:** commit 60c9b4b (= b19e95c; the user reverted the 2026-10-14 evening bundle, which is kept in 7fc95c4). On
top of it, UNCOMMITTED (the user handles git): a per-ticker layer and two CROC-only changes. CRZY and TAME run exactly
as b19e95c. 182 tests pass.

**Working rules (user):** one case at a time; judge on REAL heats first (journal replay / real-outcome counterfactual);
the simulator only as a safety check (rival desks); focus on CRZY / TAME / CROC.

**What is new:**
1. `LiabilityStrategy.tp(ticker, key)`: `[strategy.per_ticker.<TK>]` overrides any strategy key per stock (valuation
   and sprint use it).
2. VALUATION, CROC: `edge_floor = 0.15` (`LiabilityStrategy.edge_floor`): accept a fixed CROC tender the cost model
   declines when its edge vs the mid is >= 0.15, but NOT while the crowd alarm is on (crowd guard). 22 real heats:
   the model declined 40 of 99 tenders with edge > 0.10; 75% of those would have made money (+$207k), charged 0.49/share
   vs a real cost of 0.13. CROC floor + guard: +7 tenders, +$66k, 0 losers. Sim (26 markets): +$3.2k (t 4.6); left:
   one "impact: trend + rivals" seed. Log line: `ACCEPT (edge floor: ...)`.
3. EXECUTION, CROC: `soft_trend = true` replaces the fast 10-tick sprint on CROC: the 60-tick trend against the block
   (own crossing removed), in units of CROC's own noise: >= 1 sd rest one tick inside, >= 2 sd also cross <= 1,500
   sh/tick. 14 real heats: CROC sprints -$77k vs expected; resting on the same price path +$29k better (7 of 9).
   Log line: `SOFT CROC: ...`.

**Key real-data facts (36 real heats on 2026-10-14):** rested blocks always beat their expected value (+$53k, 0 losers
in 14 heats); sprinted blocks -$106k (CROC -$77k, TAME -$37k; TAME sprints still beat resting on the same path).
All three stocks TREND (no mean reversion); TAME/CRZY fast momentum (r 0.54/0.46), CROC slow (60-tick r 0.45) and the
noisiest tick to tick. Evening heats with 3-5 other teams online: post-tender move -0.06..-0.11 against (sim rival
markets -0.31): a moderate crowd. Tools (session scratchpad, re-create if needed): journal_replay.py (feeds a journal's
market rows through the strategy; live=True; exact decisions, no fills), verify_step.py (decision diffs + real
outcomes). Old-layout journals (logs/liability-*.jsonl) have no market rows and cannot be replayed.

**SIM SAFETY CHECK OF `soft_trend`: FAILED** (`docs/results/ab_croc_soft_2026-10-14.txt`; arm0 today's CROC sprint,
arm1 CROC sprint off, arm2 soft trend; seeds 1-16). Soft trend pooled -$4.1k (t -5.6): trend markets -$4k..-12k with the
worst heat +$4.6k -> -$39k, rival markets -$5k..-7k, "everything at once" -$12k. Sprint off -$2.4k (t -3.9). The sim
says a CROC block caught in a STRONG trend needs a fast exit; gentle crossing (<= 1,500 sh/tick at 2 sd) is too slow.
Real data (9 CROC sprints) said the opposite, but our 14 heats may lack a strong CROC trend against a block. The user
was told to run the batch with `--set strategy.per_ticker.CROC.soft_trend=false` (edge floor only). The committed
config a731c81 still has soft_trend = true: set it to false (or redesign: faster gentle crossing / exit at 2 sd, then
re-test) before the next default run. Replay tools: `docs/results/journal_replay.py`, `docs/results/verify_step.py`.

**Next:** the user archives the logs and runs a batch of this version on Windows (copy .log + zipped logs\data;
note how many other teams are online). Then: replay every new heat through the old bot (`--set
strategy.per_ticker.CROC.edge_floor=0 --set strategy.per_ticker.CROC.soft_trend=false` = b19e95c) and compare per
tender / per block. Open cases after that: (0) FLAT FIRST 100 TICKS (user's question): only tenders #2 (~tick 20) and #102 (~85) can
make money before tick 100 (auctions 0/92 won). 22 heats: #2 taken 70% (mean edge +0.30; 7 declined with edge > 0.10,
crowd alarm off), #102 taken 57%; both declined in 5/23 heats -> nothing until ~tick 140. Cause: warm-up valuation (the
adaptive gate needs 2 scored tenders, so early tenders are priced by the pessimistic book walk; drift/vol just started;
a trend after #2 can trip the crowd alarm before #102). The CROC edge floor already applies from tick 0; TAME/CRZY early
tenders are not fixed yet: next valuation case "warm-up valuation". TAME valuation (evening-only gains so far), the crowd learner learning only
from untaken tenders (starves the adaptive gate when we accept more), TAME sprint in crowded markets.

---

# Handoff: 2026-10-14 (START HERE)

8 practice heats reviewed (RESEARCH.md "Sprint walk cap and own-impact run"). Default unchanged; new code, all OFF by
default: `execution.sprint_max_walk` (sprint price cap), `strategy.run_ex_own` + `own_impact` (trend run without our
own impact), sim knob `real_impact` + stress group `impact`. All three fail the testing rule (sim says sprinting pays).
Fine text/Excel now $0.20 first 5,000 / $0.60 beyond (sim + replay already were). 178 tests pass. Uncommitted.
Next: default-only practice heats + `ritc record`; measure the mid's reversion after each CROC sprint.

---

# Handoff: state at the end of 2026-10-13 (START HERE; older notes below are history)

**Nothing committed.** All work is uncommitted on `main` (ask before commit/push); Windows needs it pulled.
177 tests pass (`PYTHONPATH=src .venv/bin/python -m pytest -q`). Details + every A/B table: docs/RESEARCH.md
(sections dated 2026-10-13 evening/night, newest first).

**Root cause of losing unwinds = TREND (confirmed).** 3 practice heats with profile F_root_cause + `ritc record`
(logs/record-20261013-*.jsonl): 10-tick momentum r 0.46 (t 8.5), variance ratio 1.5-2.4; hold tests (no trading
30 s) moved our way 5/6 -> not "holder drift", not our impact. Resting unwind gets stuck when a run goes against
the block. Book also predicts: depth asymmetry (cost to cross 25k ask - bid) t 7.6, OFI t 4.2 beyond it.

**New defaults (config/liability.toml), each tested design 1-24 + holdout 101-124 (scratchpad ab.py harness):**
- `trend_learn/trend_exec`, `trend_cross_cost 0.05`: learn r live (prior 0.46), SPRINT = cross when expected run
  against block > cost. Measured-trend sim +$16k design / +$9.8k holdout (pooled holdout only t 1.2; user chose on).
- `book_signal`, `book_gate_t 2`: predictor uses depth asymmetry (+ OFI, `book_models="ofi"`, user chose on) once
  the heat's own t >= 2 (real heats: tick 50-110). Pooled +$1.3k (t 2.9) / +$0.6k (t 2.1).
- `initial_sprint_ticks 3`, `initial_sprint_gate "crowded"`: race the crowd at block start only when rivals are
  detected. +$3.6k (t 5.7) / +$2.2k (t 4.8); rival markets +$1-17k, solo $0. Fixes the weak rival case.
Rejected/off: crossing more (hold_risk_budget), liquidity_aware valuation, reduce_in_windows (strict-fine risk
-$18k), auction_skip_side=any, netting_credit (profile D only), live_cross_cost, trend_value, ungated OW start,
holder_drift_learn, stall_and_trend.

**Tooling added:** sim knobs mm_inv, edge_sd/edge_mu, hold_drift, kyle_fill_tilt, book_tilt; stress groups
`real2`, `trend` (scenario names have no commas); `ritc calibrate` ROOT-CAUSE TEST; `execution.hold_test_ticks`
experiment (profile F_root_cause); SPRINT log lines explain each cross. Profiles: D_solo_netting, F_root_cause.

**Next:** (1) commit + push after user OK, pull on Windows; (2) practice heats with the DEFAULT bot (no
--profile; `rotate` would pick D/F) + `python -m ritc record`; check SPRINT lines fire on real adverse runs and the
crowd race fires only with other teams; (3) `ritc calibrate logs/record-*.jsonl` pooled to keep the trend/book
evidence growing. User goal: >= $50k every heat in solo and competitor markets (solo practice heats made $42-57k).
Unresolved: docs/practice_logs/*.txt show as deleted in git (not by Claude); `ritc record` time & sales came back
empty (trades/tick 0).

---

# Handoff: state at the end of 2026-10-12 (start the next session here)

**Update 2026-10-12 (review session):** PR #5 (`liability-fresh-unwind`) is merged into `main`; local `main` was
fast-forwarded. New on top, UNCOMMITTED: `strategy.room_wait = true` (+$4.9k a heat, t 3.6, never worse; RESEARCH.md
"Auction clues"), sim knobs `reserve_lo` / `reserve_hi`, 160 tests. Auction clues to collect in practice, in order:
(1) the `caption` of tenders 700-703 in the `.jsonl` journals (is id 702 the competitive auction and the others
winner-take-all?); (2) `ritc report` on the journals: each auction's bid vs mid and won/lost = the real reserve;
(3) try re-bidding an auction once (does a second POST change the bid?). Then set the auction bid per type.
**Models tried after that (same day), all rejected:** auction reserve learner, Kyle-coupled simulator + VPIN unwind
rule (RESEARCH.md; code in `docs/rejected/`). To revisit VPIN, first run `ritc record` in a practice heat and read
`ritc calibrate`'s "signed trade flow -> next 10-tick move" lines.

**End of 2026-10-12:** a plain-language brief of the whole liability algorithm (pipeline diagram, valuation formula,
decision rules, auctions, unwind, risk controls, practice results, models kept/rejected, questions for the professor)
is in the user's Claude doc "Liquidity Risk Case: Bot Algorithm Brief" (https://claude.ai/artifact/CMcCv8tLH5qMZjSA7H2rFf).
Still uncommitted on `main`: room_wait, sim options, calibrate flow lines, docs, `docs/rejected/` patch. 162 tests.

Liquidity Risk Case bot (`liability`), graded heats **next week**. Read in this order:
1. this file
2. [PRACTICE_LOGS.md](PRACTICE_LOGS.md): what the five real practice heats showed (raw logs in `practice_logs/`)
3. [PRACTICE_PLAN.md](PRACTICE_PLAN.md) "Judgement week": the plan and the decision rules
4. [ASSUMPTIONS.md](ASSUMPTIONS.md) section 1b (measured on the real server) and section 6 (open questions)
5. [WINDOWS_SETUP.md](WINDOWS_SETUP.md): how the user runs it (Windows + RIT client)
6. [CHANGES.md](CHANGES.md) (2026-10-11 / 10-12 entries) and [RESEARCH.md](RESEARCH.md) (what was tested and rejected)

## Where the code is

- Branch `liability-fresh-unwind`. The user's last commit is `6c063c8 "connection"`; everything after it was
  **uncommitted at the end of 2026-10-12** (check `git status`). GitHub `main` is older still.
- **The Windows machine has none of the 10-11 evening / 10-12 work** (duplicate-order fix, $0.60 fine,
  liquidity model) until it is committed, pushed and pulled (WINDOWS_SETUP.md section 0-1).
- Tests on the Mac: `python -m venv <scratch>/venv && <scratch>/venv/bin/pip install -r requirements-dev.txt`,
  then `PYTHONPATH=src <scratch>/venv/bin/python -m pytest -q` → **159 passed**; `ruff check src tests` clean.
  Tools: `PYTHONPATH=src python -m ritc ...` (miniconda python). The repo `.venv` also works (`pip install -e`).
- The Mac `.env` has the user's DMA settings plus `RIT_DRY_RUN=false` and `RIT_MIN_INTERVAL=0.1`. Tests pin their
  own environment (`tests/conftest.py`) and lock-step tune / stress / replay ignore the throttle, so this is safe.

## What was done (2026-10-11 → 10-12)

| Area | Change | Status |
|---|---|---|
| Manual trading | `ritc suggest`: read-only live advisor (tender ACCEPT/DECLINE/BID + when, limit orders to type, fine warnings); waits for the API and re-reads `.env` | in use |
| Connection | DMA API support (`RIT_AUTH` header); `Runner` waits for `/trader` before starting | DMA port 10002 was closed on the practice server; the user uses the RIT client API on Windows |
| **Real-server bug** | real RIT books orders asynchronously → the unwind re-sent orders → positions went past zero (speculation fines). `Executor.in_flight` / `execution.order_settle = 1.0` + cancel of lost own orders | **adopted**, tests replay the log pattern; costless in the real-time sim |
| Rules | speculation fine past 5,000 shares $0.40 → **$0.60** (organisers) | sim + report |
| Tender timing | `decide_late_ticks = 0` (answer at once), the user's choice | calibrated sim: answering late only +$4k (t 0.9) |
| Simulator | `RITC_STRESS=real=1`: calibrated to the practice heats (tenders 50-59k every ~35 s, windows 30-31, 36% auctions, wide edges, real vols, resting absorption 2,600 / 3,200 / 1,500 shares/tick). Reproduces the real auction win rate and fixed-tender count | use it for every new A/B |
| **Liquidity (the professor's point)** | forward liquidity forecast from time & sales; `strategy.liquidity_model = "flow"` prices tenders at our resting touch over shares ÷ flow | **aggressive profile**, default OFF: 64 fresh calibrated heats +$5.7k (t 2.9); crowded market worst heat −$52.8k → −$61.1k |
| Auctions | root cause found (below); `liquidity_model = "auctions"`, `auction_max_net` | **rejected** (code kept as options) |
| Tooling | in-flight test harnesses in the session scratchpad (`realcheck`, `auction_rootcause`, `auction_pnl`): not in the repo; `ritc tune` / `postmortem.replay` reproduce them | — |

## What the real case taught us

1. **Fixed private tenders are the money**: ~$0.55/share edge on the ones taken, ~$27-29k each. 11 tenders a heat,
   50-59k shares (half the 100k net limit), windows 30-31 s.
2. **Liquidity is huge and fast**: resting unwinds absorbed 50k in 15-25 s (2,500-3,600 shares/s) at ~the touch. The
   bot's book-walk valuation is ~$0.19/share too pessimistic.
3. **The two big losses were liquidity failures**: unwinds where our side stopped filling (700-900 shares/s) while
   the price ran (−$1.76 and −$0.83/share). Volatility was not the problem.
4. **Auctions: won 2 of 18.** The bid stacks ~$0.24/share of haircuts (book walk, risk/drift, margin, fee); the
   reserve is nearer the mid. Bidding closer wins 14% instead of 1% but **loses** $4-7k a heat: each won 55k block
   crowds same-direction fixed tenders out of the net limit (replay: fixed booked 113 → 108, −$184k vs +$52k from
   auctions). Bidding up only when it costs no room: no loss, no gain.
5. **$40k vs the leader's $200k** in the last heat: mostly private tenders differing by team (one good tender that
   heat) and variance (holding 50k through a $2.5 move is ±$125k). The calibrated sim spreads the same bot over
   $0-210k a heat.
6. CROWD detection stayed ≈ 0 in 4 of 5 heats; the last heat's gate opened on TAME's trend (a false positive).

## Open questions (ASSUMPTIONS.md section 6)

1. **Scoring of the graded heats** (per heat / total / risk-adjusted): decides safe defaults vs the aggressive
   profile. Ask the organisers.
2. Close-out fine at the bell (A15), front-running fines, commission on resting fills: read RIT's Transaction Log
   after a practice heat.
3. The real auction reserve: needs the `.jsonl` journals (mid at every bid) from Windows `logs\`.

## Tomorrow, in order

1. **Ship the fixes to Windows**: commit + push on the Mac (ask the user before pushing), `git pull` + `pytest`
   on Windows (WINDOWS_SETUP.md).
2. Practice heats per PRACTICE_PLAN.md "Heat plan": A defaults (check no "past zero" positions, fines 0),
   B aggressive profile (`--set strategy.liquidity_model=flow`), C defaults for auction-reserve journals.
3. Get the `.jsonl` journals + Transaction Log screenshots from the user; `ritc report` on them; answer open
   questions 2-3; set the profile for the graded heats with the PRACTICE_PLAN decision rules.
4. Next research step, if the user agrees: couple order flow to price in the simulator (Kyle), then A/B a
   VPIN-style "cross when our side stops filling" unwind rule (RESEARCH.md, microstructure review). It targets
   finding 3 above.
5. Testing rule (always): keep a change only with paired t ≥ 2 on design seeds AND a fresh holdout, checked in the
   calibrated (`real=1`), default and hostile (`real=1,anchor=1 --hostile 1`) markets.
