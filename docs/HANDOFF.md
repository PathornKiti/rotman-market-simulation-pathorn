# Handoff: 2026-10-15 (START HERE)

**Goal and scoring (user):** the graded heats on Sunday 2026-10-18 are ranked on AVERAGE heat P&L; the target is top 3
consistently. Expect a crowded market (rivals unwind the same tenders). Leaderboard is admin-only. Speculation fine:
$0.20/share for the first 5,000 shares, $0.60 beyond.

**Frozen bot unchanged** (`config/liability.toml` = config of b04253e, `scripts/review_batch.py check` says OK).
Feedback "the valuation is too pessimistic" was screened: 11 single changes, each alone vs the frozen bot, on the 17
real evening journals (replay) and in the sim (49 markets x 16 seeds). Full table: docs/results/screening_2026-10-15.txt + screening_2026-10-15/.
- Across-the-board cuts in pessimism (`flow`, lower `risk_aversion`, softer or no crowd charge, no drift) bring back
  tenders that lose in crowded markets: the crowd charge and the drift term decline mostly losers in real crowded heats.
- Targeted fixes pass: `drift_horizon_ticks = 20` (B; real +$1.0k t 0.6, sim +$2.1k t 7.7, crowded +$0.1k) and the new
  `drift_zero_start` flag (EWMA starts at 0, not the heat's first tick; sim +$0.5k t 3.9, crowded +$0.2k, real 0).
- Unwind: crossing more (`hold_risk_budget` 20k) -$1.9k t -7.4; front-load / shorter horizon / hold-all: no effect
  (the rest-only budget governs most unwinds). Sprint at $0.03 (H): crowded +$0.7k t 2.6 but quiet markets -$3-9k.
- Tender edge is capped at ~$100k a heat (17 journals: mean $92k, max $108k offered), so rivals' $200k heats are
  price-move luck; the crowd's push after a tender does not revert within 2 minutes.

**Live rotation** (`config/liability_profiles.toml`, RUNBOOK section 6, WINDOWS_SETUP section 5):
`ritc run liability --live --forever --record --profile rotate` cycles A_default, B_drift_cap20, C_flow, D_risk015,
H_sprint003 (move the old logs folder away first: rotate counts every journal). On a quiet practice server C and D will
look good and H worse than on Sunday; judge C/D/H on the sim's crowded rows, B on its flips + blocks.

**Uncommitted (user handles git):** liability_profiles.toml, RUNBOOK.md, WINDOWS_SETUP.md, HANDOFF.md,
`drift_zero_start` in strategies/liability.py + its test, tests/test_research.py, scripts/review_batch.py,
docs/results/screening_2026-10-15.txt. Harnesses: docs/results/screening_2026-10-15/ (valcf.py = replay flips for any override, pair.py = paired sim arms, arms.sh).

**Next:** review the rotation heats (`review_batch.py review`, `ritc practice`, per-profile flips and blocks). Sunday
candidates: A + B, then drift_zero_start; H only if Sunday is crowded and its live sprints look right.
Open: how many graded heats on Sunday (luck vs average).

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
