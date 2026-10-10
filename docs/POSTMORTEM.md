# Liability bot: reviews and reports

Brief: `document/Liquidity Risk Case - for selection 2027.pdf`. Bot: `liability`. Reports: `reports/index.html`
and one `reports/liability-<date>-<time>_<local|live>.html` + `.xlsx` per run (`python -m ritc report`).

Three reviews of the Liquidity Risk Case bot, newest first, and what each report contains. Assumptions and their
checks: [ASSUMPTIONS.md](ASSUMPTIONS.md); practice-day plan: [PRACTICE_PLAN.md](PRACTICE_PLAN.md).

## The reports

| Command | Files | What is in them |
|---|---|---|
| `python -m ritc report` | `reports/liability-<date>-<time>_<local\|live>.html` + `.xlsx` per bot log, `reports/index.html` | gap checks against the brief; from the run journal: P&L path, where it came from, the struggle distribution, tenders; for local runs also the simulator's truth and the current bot replayed on the same market with per-tender what-ifs |
| (same) | `reports/<log>_<mode>.json`, `reports/live-learnings.json` | each run's P&L, P&L parts, tender counts and struggles (count, $, severity, what to look at), for the next review |
| `python -m ritc report --stress` | one `reports/stress-<date>-<time>_local.html`, `.xlsx` and `.json` | the current bot through every stress scenario, every seed side by side, vs the base market and the official template; per seed a "what happened" summary and the bot's full log |

`_local` = the offline simulator (found from the tender stream, a non-RIT tick rate or port), `_live` = the RIT
server (`--mode` overrides). Every report opens with a plain-language explanation of the case. Every `.xlsx` has the case help file's layout (`Tender replay`: pick a row, see the
tender and the books, priced off the book with formulas) and a fixed, extended live RTD sheet.

**Struggles** the journal analysis counts: tenders unanswered, answered at the last tick, tender action failed,
auction bid rejected, accepted but never booked, slow booking, declined 10c+ inside the mid, orders inside an
undecided window, position through zero, near the limits, API errors by type, slow loops, drawdown, held at the
bell. Each has a "look at" pointing to the setting or improvement below.

## 2026-10-10: review of the eight liability logs

### What was analysed

All eight bot logs in `logs/` are **local** runs (the offline simulator): every one's tender stream matches a
simulator seed, so each market could be rebuilt exactly (true mid, hidden auction reserve, winner-take-all
rival, decision windows) and the **current** bot replayed on it, fully instrumented.

| Run | Bot version | Orders | Seed / market | What it was |
|---|---|---|---|---|
| 20261009-224656 | v1 (answer at once) | live | 7, base | joined at tick ~106, stopped by hand at ~380 |
| 20261009-225906 | v1 | live | 5, base | full heat; the bot "crashed" only because the simulator exits at the bell |
| 20261009-230550 | v1 | dry | - | one loop, no case running: nothing to analyse |
| 20261010-100406 | v1 | dry, 3 bots in one file | 11: hostile anchored, base, crowd at expiry | real-time stress dry runs |
| 20261010-100458 | v1 | dry, 4 bots in one file | 12 and 13: base, crowd at expiry | same |
| 20261010-104620 | v2 (answer late) | live | 777, base | real-time test, 2 ticks/s |
| 20261010-105313 | v3 (late + end guard) = current | live | 777, base | the same market after the end-of-heat fix |
| 20261010-111656 | v3 | dry | 31, base | simulator at ~19 ticks/s |

### How

1. Parse every decision, order, block, crowd / vol line, error and TCA table (all three log formats; several
   bots in one file kept apart by tender id, side, stock and size).
2. Find the seed (dry run: tender id k is the k-th tender of the stream; live run: the logged tenders appear in
   order) and the market variant (from the fixed tender prices).
3. Check the run against the brief (below), with the simulator's truth where it matters.
4. Replay the current bot on the same seed and variant: an exact P&L decomposition (tender edge vs mid, resting
   and crossing fills vs mid, fees, own impact, inventory moves, close-out, fines; the parts add up to the score)
   and per-tender FIFO attribution.
5. Counterfactuals: flip each decision on its own (accept a declined tender, win a lost auction at the hidden
   reserve or 1c past the rival, decline an accepted one) and replay. The change splits into **skill** (every
   part except inventory moves) and **luck** (what the random walk did to the extra or missing position).
6. A/B the candidate fixes on 16-96 paired seeds, the repo's usual rule (t >= 2, holdout, no worse tail, hostile
   market not worse).

### Findings, against the brief

**Objective 1, tender selection: the bot declines large blocks that are well inside the mid.** It prices a
tender by walking a 2x book with taker fees for the whole block, but its own unwind is mostly resting orders
and the free close-out: the current bot crossed nothing in 10 of 12 replays (at most 20k shares and $404 of
commissions, in a crowd-at-expiry market).
Every run shows declined 30-50k private tenders 10c+ inside the mid (e.g. seed 777: 4 tenders, $32.5k of gross
edge). Pooled over the five base-market seeds, flipping those declines adds **+$6.3k a heat of skill (5/5
positive)**, small-edge declines another +$4.9k a heat; the accepts and the no-edge declines are right
(flipping them costs $51.5k and $30.5k a heat).

**Auctions: most bids miss the hidden reserve.** 0 of 10 bids would win in run 224656, 0 of 5 on seed 777, by a
median 13-34c; one winner-take-all bid cleared the reserve but lost to a rival. Winning at the reserve is worth
up to +$2.9k a heat in a calm market, but a bid priced off the mid overpays (A/B below), so the fix is
information (where reserves sit), not a lower margin.

**Objective 2, execution: resting fills lose a little to adverse selection.** 93k of 161k resting shares on seed
777 filled as the price ran through the order; resting fills net -$1.1k to -$3.9k a heat vs the mid. Resting
further from the touch did not help in the simulator (fewer fills, worse tails): a practice-day measurement.

**Timing.** The late answer (3 ticks before expiry) needs loops well under 3 ticks: the 19-ticks/s dry run
answered 20 of 31 tenders. At RIT's 1 tick/s that margin holds, but slow loops cost money even when every tender
is answered (-$9.5k a heat at 3 ticks per loop).

**Coverage.** Run 224656 joined at tick ~106 and missed tenders 1-2 (one was 10c inside the mid).

**Fines.** v1 logs traded a stock inside our own auction bid's window (18 orders in 224656, strict reading) and
overshot CRZY through zero by 2,400 shares (~$480 fine plus a round trip): both fixed, the current bot opened 0
speculative shares in every replay. One exposure remains: in 6 of 12 replays the current bot traded 5,000-17,400
shares while a tender on that stock was undecided, every one a resting unwind order filled **in the very tick a
new tender arrived**, before the bot could see the tender and pull its orders. All of them reduce the position,
which the brief permits ("only trades that reduce an existing position from an accepted tender are permitted
without penalty"), so they are fine-free on that reading; on the strict reading ("any trades executed during
this window") they would cost up to ~$6k a heat. No bot change can see a tender before it arrives: check the
first live practice heat's Transaction Log (ASSUMPTIONS.md A10).

**Bell.** The current bot ends heats holding inventory (seed 777: 35,000 CRZY; peak 1-sd risk $25k), deliberately,
because the 2027 brief closes positions at the last price with no fine (ASSUMPTIONS.md A15: confirm in practice).

**The case's help file** (`document/Liquidity help file.xlsx`) splits TENDERINFO using the LEN of values it has
already parsed, so a price sent as `24.50` mis-parses every field after it; it labels CROC as security 2, shows
only the first open tender, and has no tender evaluation or limit check. Each report's `.xlsx` carries a fixed
and extended copy (`Live (RTD)` sheet) and the same layout replaying every tender of the run (`Tender replay`).

### Improvements (trading, not logging)

| Change | Evidence (paired simulator A/B) | Verdict |
|---|---|---|
| Keep answering tenders in the last 5 s (`wind_down` calls `handle_tenders` while holding to the bell; `min_ticks_to_unwind = 1`) | seeds 33-96: +$0.9k (t 3.14), hostile +$0.1k, worst heat unchanged; seeds 1-32 +$0.4k (t 1.53) | **adopt** |
| Answer-deadline safety net: answer now if the measured ticks per loop say the next loop would miss the window | identical at normal speed (64 seeds, both markets); 4 ticks/loop +$9.8k (76% -> 99% answered), 5 ticks/loop +$17.8k | **adopt** (insurance) |
| `refill_factor` 3 + `min_profit_per_share` 0 | base +$4.5k (t 3.40), holdout +$3.6k (t 2.99), worst unchanged; hostile +$0.3k / +$1.3k; crowd at expiry -$3.5k, worst -$27k -> -$53k (seeds 1-32) | **adopt if practice shows no crowd at expiry** |
| Value the held part of private tenders at the mid | base +$3.5k (t 1.56), hostile +$2.3k, crowd at expiry -$9.3k (t -2.69) | conditional, weaker than the row above |
| Bid auctions off the mid | -$1.2k / -$0.5k (t -2.86) / -$3.7k | rejected |
| Rest one tick behind the touch / never step inside the spread | +$0.2k / +$0.5k, worst heat $20.0k -> -$4.9k / $5.6k | rejected |
| Start the bot before the heat opens | run 224656 lost a quarter of its heat | do now (operations) |

Not adopted into the bot in this review (no code in `src/ritc/strategies` changed): the first two rows are
small, flagged changes; the third is two config values (`--set strategy.refill_factor=3 --set
strategy.min_profit_per_share=0`) that should wait for the practice crowd check (PRACTICE_PLAN.md).

### Practice day: what decides the open rows

- **Crowd at expiry, by tender type** (`ritc calibrate` crowd event study): the brief says private tenders are
  "assigned directly to individual participants" and arrive "at different times", so a crowd should only follow
  auctions. No crowd at expiry on private tenders -> adopt the valuation row.
- **Auction reserves**: every `success` / `not filled` brackets one reserve; record where they sit vs the mid.
- **Resting fills**: `ritc record` + `calibrate` fill model, before moving where the unwind rests.
- **Close-out**: no fine on positions open at the bell (A15).
- **Front-running scope**: are reducing fills in the tick a tender arrives flagged (Transaction Log)?

### Reproduce

```bash
pip install -e ".[report]"
python -m ritc report                       # all logs, with counterfactuals (a few minutes)
python -m ritc report logs/x.log --mode live --no-counterfactuals
```

## 2026-10-10: risk review (why losing heats lose)

Exact P&L attribution of every losing heat over 32 seeds and 12 scenarios. **In the brief's market the bot does
not lose** (0 of 32; worst +$22.1k); in the volatility / worse-tender / bigger-tender stresses 1 heat in 32 lost
$0.2-2.0k on random-walk inventory. **Every material loss is a crowded-tender loss** (other desks unwinding the
same block): the pooled crowd estimate under-charges thin CRZY; holding to the bell keeps the position still
while a visible crowd runs against it; blocks accepted late have their crowd land at the bell. Pumps, spoofs,
vacuums, fines and adverse resting fills decide no losing heat.

The proposed fixes (crowd race on adverse arrival, per-ticker crowd impact, crowd position cap) looked good on
the design seeds but **all failed on fresh seeds 201-232** with tenders priced off the visible mid, and were
removed. The remaining protection is operational: set `crowd_prior_mean` from the practice crowd study.
The same review found the simulator artifact behind every earlier hostile number: tenders priced off the true
mid while the crowd displaced the visible one (+$92k a heat of fake edge), hence the `anchor=1` stress knob.

## 2026-10-11: gap analysis (simulator vs real server)

Every way the simulator might differ from the real server became a stress knob, a bot change (kept only by the
usual rule) or a practice-day measurement:

| Gap | Filled by | Result |
|---|---|---|
| Resting fills free in the sim | stress `maker_fee=1` | -$4.3k, 0 losing heats |
| 4c spread puts us first in the queue | `spread=0.02/0.01` + `--queue` | -$1.8k / -$3.1k |
| Resting fills rarer / close against holders / desks dump at the bell | `flow=0.3`, `close_slip=0.05`, `bell=0.20` | -$1.9k / -$1.4k / -$8.1k, 0 losing |
| **Tenders answered at once, wasting the 15-30 s window** | **bot: `decide_late_ticks = 3`** | **losing heats over 21 scenarios 13 -> 1; base +$25.3k (t 4.5)** |
| Windows running past the bell | bot: `decide_late_end_guard` | +$3.7k (t 4.1) |
| Auction reference price 0 / `expires` unit unknown | bot guards (unit tests) | robustness |
| Would a better vol model help? | oracle-vol test | perfect vol adds $0: not built |
| Crowd timing: other desks answer late too | `crowd_at_expiry=1` | the one remaining losing region (9/32 losing, worst -$39.8k) |

Mitigations for the crowd at expiry: a "head start" playbook (answer at once, 12-tick unwind, no hold) fixes it
(losing 5 -> 0) but costs -$34.2k a heat when there is no such crowd, so it is used only on practice evidence
(PRACTICE_PLAN.md). Re-scoring the crowd at expiry, crowd priors and a fixed crowd charge were rejected.
