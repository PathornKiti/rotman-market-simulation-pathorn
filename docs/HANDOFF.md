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
