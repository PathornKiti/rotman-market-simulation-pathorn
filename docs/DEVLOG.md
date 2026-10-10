# Development log and plan

## 2026-10-11: Liquidity Risk Case, "for selection 2027" brief

The organisers sent the official brief, an Excel RTD helper, the RTD and REST API docs and a Python tender
template (`document/`, `Python package/`). This week's case is the **`liability` bot** (Rotman's "LT" =
liability trading: take a client's block onto your book, then unwind it). Practice server opens Sun 2026-10-11.

| Area | Change | Files |
|---|---|---|
| Config | 2027 brief: CRZY/TAME/CROC, max orders 25k/10k/20k, net 100k; kill switch off; `respect_windows`, `booking_ticks`, `freeze_rejected_bids`, hold-to-the-bell settings, `decide_late_ticks = 3` + guards, `min_ticks_to_unwind = 5` | `config/liability.toml` |
| Liability bot | unbooked-tender tracking and `unwind_target`; undecided-window freeze; hold to the bell (passive-only window, $ risk budget, close-out valuation); late answers with expiry / end-of-heat safety; auction reference-price guard; `VOL` log line | `strategies/liability.py` |
| Execution | resting orders never larger than requested (`never_larger`); passive-only blocks | `core/execution.py`, `core/algo.py` |
| Simulator | 2027 case (prices, vols, depths, CROC regimes, end-of-case liquidity, 420 ticks), booking delay, winner-take-all, limit enforcement, last-price close, speculation / front-running fines, random tender times, time and sales; stress knobs `vol depth edge size gap book strict_fr flow anchor maker_fee spread close_slip bell crowd_at_expiry vol_regime` | `sim/server.py` |
| Tools | `ritc stress` (scenarios, frontier sweep, template benchmark, oracle-vol env), `ritc record` / `ritc calibrate` (practice measurement), `ritc run --set` | `stress.py`, `recorder.py`, `cli.py` |
| Tests | 130 → 139: auction reserve / WTA, sub-heat tickers, late answers and guards, rejected-bid freeze, anchored tenders, auction reference price, recorder fill model and crowd study | `tests/` |
| Docs | GAP_ANALYSIS (16 gaps), RISK_REVIEW (risk-manager diagnosis + validation), PRACTICE_PLAN (heat-by-heat, stop rules, playbooks, risk profiles), RESEARCH entries, OFFICIAL_RULES | `docs/` |

Work was split with two agents: a quant analyst (hold-to-the-bell, later the crowd mitigations) and a risk
manager (loss attribution). Every kept change was re-run independently before being reported.

Open questions only the practice server can answer (all measured by `ritc record` + `ritc calibrate`):
1. Is the close-out at the last price really free? (the hold-to-the-bell gain depends on it)
2. Do resting orders fill, and how far from the mid do trades reach (fill model / kappa)?
3. Is there a crowd, and does it hit on tender arrival or at expiry? (decides the head-start playbook)
4. Real tender windows, `expires` semantics, booking delay; does an auction we bid on stay listed?
5. Real vol / depth / tender edges, to recalibrate the simulator and re-run `ritc stress`.
6. The `/limits` name (brief says LIMIT-STOCK; config group is `equity`).

Next: Sunday practice per [PRACTICE_PLAN.md](PRACTICE_PLAN.md), then recalibrate the simulator from the
recordings and retune; build a resting-depth rule only if the fill data supports it.

## 2026-10-09 (later): RITC 2026 rules

The official RITC 2026 package (github.com/RotmanFRTL/RotmanFRTL.github.io) changes the case lineup:
Liquidity Risk, Volatility, Electricity (API orders off), **Merger Arbitrage (new)**, Algo Market Making.

| Area | Change | Files |
|---|---|---|
| Liability | Trades every stock `/securities` lists (2026 sub-heats change tickers); new tickers get the server's fee and max order and count in the limit groups | `strategies/liability.py`, `core/risk.py` (`add_ticker`) |
| Equity | Minute-close aggregate limit: reduce-only quoting into each close, cross the excess, limit parsed from news | `strategies/equity.py`, `pricing/news.py`, `config/equity.toml` |
| Simulator | Equity is the 2026 case (WNTR, rebates, aggregate limit + $10/share fine at each close, close news shocks) | `sim/server.py` |
| Docs | 2026 rules for liability and equity | `docs/OFFICIAL_RULES.md` |
| Tests | sub-heat tickers, 2026 equity universe, close fine, limit parser, close cuts | `tests/test_official_rules.py` |

Next, in order: derivatives on the 2026 format (one 1-month expiry, $0.01/share and $1/contract) and
the vol market maker as measured on real practice servers in Sep 2026 (holds the old vol through a week
boundary, then ~12-tick half-life; 4–14 pt stale at tick 1; 3–4 pt wobble); a hedgeability guard
near expiry; a ~1 s tender booking delay in the liability sim; a Merger Arbitrage bot; DMA API support.

## 2026-10-09: losing seeds diagnosed; queue model; in-process tuner

| Area | Change | Files |
|---|---|---|
| ETF | `fx_hedge_band` (off): flatten the USD balance with market orders. The losing/weak ETF seeds were FX on that balance (~$7.3k/heat sd) | `strategies/etf.py`, `config/etf.toml` |
| Simulator | `--queue`: price-time priority behind the displayed book (off by default) | `sim/server.py`, `cli.py`, `tune.py` |
| Simulator / tuner | `route()` shared by HTTP and `InProcessAdapter`; lock-step tuning skips loopback HTTP (identical results, ~5x faster, immune to antivirus interception) | `sim/server.py`, `core/client.py`, `tune.py` |
| Docs | OFFICIAL_RULES.md said the ETF arb is FX-neutral; it isn't | `docs/OFFICIAL_RULES.md` |
| Tests | queue model (4), FX hedge, in-process transport | `tests/test_execution.py`, `tests/test_strategies.py` |

Current baselines (benign, 1–16 / 101–116): derivatives $143.8k / $138.1k (no losing seed), ETF $35.2k / $25.9k,
equity $4.7k / $2.1k (5 losing holdout seeds, worst −$10.1k). Hostile: derivatives $142.3k / $136.8k, ETF
$47.7k / $47.9k, equity −$0.5k / −$2.6k. TEST_RESULTS.md (2026-10-07) predates these.

Open: decide the ETF FX hedge (risk vs ~$1.3k/heat). Equity can't be tuned out of its losses in this
simulator; the remaining levers (trend/jump detection) need the practice case to validate. Make `--queue`
the default? It's more realistic, and today it only changes equity.

## 2026-10-08 (later): each bot adapts to its own case's events

| Area | Change | Files |
|---|---|---|
| Derivatives | `vol_term`: per-option week-weighted vol from the news (kept, +$26–31k, t ≈ 6) | `strategies/derivatives.py`, `config/derivatives.toml` |
| Equity | `block_cut`: cut an assigned block at once (kept) | `strategies/equity.py`, `config/equity.toml` |
| Liability | crowd learning gated on evidence, learning only from tenders not taken (kept) | `strategies/liability.py`, `config/liability.toml` |
| Simulator | **auction bug fixed** (bids had to be below the reserve); random reserve; equity block transfers | `sim/server.py` |
| Removed | adaptive auction margin (n.s.), ETF USD hedge (−$1.8k / −$2.7k) | — |

Equity kill switch turned off (32 fresh seeds: +$951, t 3.0 benign). Block-side pause and run-over
pause tried and removed. Equity under the hostile simulator is still about break-even (−$0.5k).

Open: the first tender of a heat is unprotected from a crowd (no prior, by design). Equity block
transfers are an assumption (DEVLOG item 6): confirm against the brief. Liability book-refill
learning was not attempted: the simulator rebuilds every book each tick, so refill can't be
measured here.

## 2026-10-08: hostile market and crowd-aware tenders

| Area | Change | Files |
|---|---|---|
| Simulator | `--hostile` (sim/tune): pump-and-dump, spoofing, liquidity vacuums, penny-jumping, crowded tenders, competing ETF arbs; own RNGs, `--hostile 0` = unchanged market; `RITC_HOSTILE_THREATS` for attribution | `src/ritc/sim/server.py`, `cli.py`, `tune.py` |
| Liability | Crowd-impact learning + prior + adaptive race (`crowd_*`) | `src/ritc/strategies/liability.py`, `config/liability.toml` |
| Tests | hostile sim, crowd learning | `tests/test_hostile.py` |
| Docs | Threat model, damage, adopted/removed defences, playbook | `docs/HOSTILE_MARKET.md` |

Liability crowd learning: first version later found to cost money in the benign market once the auction bug
was fixed; replaced by the gated version (see the next entry).
Price band, spoof-capped books, and equity queue fighting / toxicity spreads / dislocation hold
were measured and deleted. Open: equity earns ~$1.5k under competition, and no tested
defence helps. Recheck in the practice case whether penny-jumpers are really there.

## 2026-10-07: ETF tenders, passive ETF leg; equity re-tune

| Area | Change | Files |
|---|---|---|
| Simulator: ETF | Private RITC tender offers on their own RNG (price path unchanged); accepted tenders settle in the quote currency (USD for RITC) | `src/ritc/sim/server.py` |
| ETF bot | `tender_edge` (basket-hedge value of a tender) + `handle_tenders`; `maker_quotes` + passive RITC bid/ask (`maker`) | `src/ritc/strategies/etf.py`, `config/etf.toml` |
| Tests | tender/maker pricing, sim tenders, tender RNG isolation | `tests/test_strategies.py`, `tests/test_official_rules.py` |

ETF $30.2k → $35.2k, worst $13.4k → $21.7k; both changes confirmed on seeds 101–116 (CHANGES.md).
Equity: no setting beat the config (RESEARCH.md).

Still open for ETF: the holdout set has one losing seed (−$1.5k); FX hedging of the USD
balance (the arb's ETF leg leaves up to ~$1.8M USD short) is untested; converters are manual only.

## 2026-10-06: official case rules

Source: official RITC 2019/2020/2023 case packages, mirrored in
[LiChiLin/Rotman-Trading-Competition-2024](https://github.com/LiChiLin/Rotman-Trading-Competition-2024)
(no licence, so nothing vendored). Full rule table: [OFFICIAL_RULES.md](OFFICIAL_RULES.md); results: [CHANGES.md](CHANGES.md).

| Area | Change | Files |
|---|---|---|
| News parser | **Bug:** the official *"between 27-30%"* forecast was ignored. Now read, along with *"penalty percentage is 0.5%"* | `src/ritc/pricing/news.py` |
| Simulator: derivatives | 10 strikes 45–54, 2-cent spreads with deep books, $0.02/share and $2/contract fees, RTM 50k limit group, official news wording, the forecast range is about the vol drawn for next week (fixes old item 4), per-second delta penalty | `src/ritc/sim/server.py` |
| Simulator: ETF | USD currency; RITC quoted in USD (`P_RITC × USD = BULL + BEAR`), BULL $10 / BEAR $15 / RITC $25, $0.02 fee / $0.01 rebate, ETF counts 2× in limits, ETF closes at NAV | `src/ritc/sim/server.py` |
| Simulator: all | per-currency cash, per-security tick size, limit groups count only their own tickers | `src/ritc/sim/server.py` |
| Tuner | scores **NLV − penalties** (what the judges rank) | `src/ritc/tune.py` |
| Configs | derivatives `option_fee` 2.00 + RTM limit group; ETF fee 0.02, `fx_ticker = "USD"`/`divide`, RITC weight 2.0; liability `decline_explicitly = true` | `config/*.toml` |
| Tests | official headlines, fees, limits, penalty, USD settlement, NAV close-out | `tests/test_official_rules.py`, `tests/test_pricing.py` |

### Open items from the official rules
- ~~**ETF case tenders**~~: done 2026-10-07 (`tenders = true`, +$3.7k).
- **API orders disabled** in the 2019/2023 Liquidity Risk and the BP Commodities packages.
  If the 2026 brief says the same, `liability` runs as a dry-run decision aid and `commodity` doesn't apply.
- **CAPM case** (2024): recorded data in the mirror (`Algo_CAPM/data/`, 12 sessions) can back a replay test.

## 2026-10-05: fair backtesting and risk review

**Status:** all changes are uncommitted on `main`. 92 tests pass, ruff is clean.

### What changed

| Area | Change | Files |
|---|---|---|
| Simulator | Passive fills use **common random numbers**: flow is keyed on (seed, tick, ticker, side) and shared in queue order. Every setting meets the same order flow, so paired A/B noise falls 3.5–6×. | `src/ritc/sim/server.py` |
| Tuner | The baseline always runs. Rows show the paired diff vs base with SE, t and seeds beaten, plus `CVaR25` and `neg` (losing seeds). Ties are nobody's win. Default seeds 5 → 16. | `src/ritc/tune.py`, `src/ritc/cli.py` |
| Derivatives | Kill switch $40k → **$80k** (catastrophe-only). The $40k stop sold a temporary vol-arb dip (seed 11: +$40.3k → −$14.0k). | `config/derivatives.toml` |
| Tests | Fill pairing (fails on the old simulator), paired stats, tie handling, tail metrics | `tests/test_execution.py` |
| Docs | Fair-testing protocol; ablation of every kept feature; final results; rejected ideas | `docs/PERFORMANCE.md`, `CHANGES.md`, `RISK.md`, `RESEARCH.md` |

### Tested and removed (didn't improve results)

- Full Avellaneda–Stoikov quoting (equity): −$1.3k to −$2.6k, t down to −8.
- VAMP / plain mid fair value (equity): not significant (t ≤ 1.0).
- Scenario stress test + CVaR position limit (derivatives, from the lecture notebooks):
  every level made both the mean and the tail worse.

### Final results (16 seeds, fair simulator)

| Case | Mean | Worst | CVaR25 | Losing seeds |
|---|---|---|---|---|
| Liability | $42.1k | $23.5k | $26.5k | 0 |
| ETF | $29.3k | $11.0k | $13.1k | 0 |
| Derivatives | $21.7k | −$1.8k | $5.5k | 1 |
| Equity | $6.8k | $3.5k | $4.9k | 0 |
| Commodity | $393 | $260 | $312 | 0 |

### How to test a change from now on

1. `python -m ritc tune <case> --grid section.key=a,b` (seeds 1–16).
2. Adopt only if **t ≥ 2** and the worst seed / CVaR25 is no worse.
3. Re-run the winner alone with `--first-seed 101`. It must hold there too.
4. Record the numbers in `docs/CHANGES.md` (kept) or `docs/RESEARCH.md` (removed).

## Development plan

### Next session
1. **Commit** today's work on a branch and open a PR. Nothing is committed yet.
2. **Derivatives seed 3 (−$1.8k):** the last losing seed. Read its logs
   (`python -m ritc sim derivatives --seed 3` + `run --live -v`) and classify the loss:
   fees, hedge slippage, or a vol-regime switch.
3. **Derivatives `exit_edge` 0.01 vs 0:** +$7.1k but t 1.06, driven by one +$88k seed.
   Settle it on 48 seeds (1–16 + 101–132) before deciding.

### Simulator realism (makes more ideas testable)
4. ~~**Vol forecast news bug**~~: done 2026-10-06 (next week's vol is drawn in advance and
   announced in the official wording).
5. **Queue position:** fills at the touch ignore queue and price improvement, so spread
   width can't be tuned. Model a FIFO queue at each price level.
6. **Equity inventory shocks:** the real case transfers unhedged blocks to market makers.
   Without them, the liquidation logic (`hard_inventory`) is never exercised.
7. **Book signals:** book sizes are random, so microprice / imbalance / VAMP can only be
   judged in the RIT practice case. Don't add informed flow tied to imbalance here: it
   would only confirm whatever we built in.

### Before the competition (RIT practice case)
8. Re-measure the worst normal drawdown per case and rescale every `max_drawdown`
   (catastrophe-only: about 1.5–2× that drawdown; derivatives and ETF especially high or off).
9. Recalibrate `ac_risk_aversion` (liability) to the case's scale.
10. A/B the equity imbalance lean and `vol_model = "garch"` on the real book.
11. If the competition has a CAPM / news-forecasting case, it needs a new strategy (none exists).
