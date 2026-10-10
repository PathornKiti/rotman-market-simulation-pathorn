# Research-grade models: what was tried and what earned its place

Only techniques that improved results are in the code. Everything else was measured,
recorded below with its numbers, and removed. A technique is adopted only if it beats the current, already-tuned setting on the
**lock-step** simulator. That simulator is reproducible: the same seed is the same market
for every setting compared (8 seeds, `python -m ritc tune <case> --seeds 8`). Mean and
worst-seed final NLV are reported because a model that raises the mean by fattening the
tail is not an improvement. Simulator P&L is noise-driven, so read the numbers as
direction, not as a forecast.

## Adopted (on by default)

### Almgren–Chriss optimal execution (liability)

Almgren & Chriss (2000), *Optimal execution of portfolio transactions*. Unwinding a tender
block trades off market impact (go slow) against price risk while holding (go fast). The
mean-variance optimal path is

```
x(t) / X = sinh(κ (T − t)) / sinh(κ T),    κ = √(λ σ² / η)
```

- **σ** is the GARCH price volatility per tick.
- **η** is the temporary impact implied by the visible book density: η = 1 / (2ρ), where ρ
  is the shares of depth per $ (`book_eta`).
- **λ** is the risk aversion.

Code: `core/algo.py` (`ac_kappa`, `book_eta`, `schedule_position(kappa=)`), wired in
`strategies/liability.py`.

| Schedule | Mean NLV | Worst seed |
|---|---|---|
| old `front_load = 0.3` | $34.1k | $16.9k |
| AC λ = 1e-4 | $31.4k | $15.6k |
| AC λ = 1e-5 | $35.5k | $19.3k |
| AC λ = 1e-6 | $37.3k | $21.7k |
| **AC λ = 3e-7 (default)** | **$37.6k** | **$22.5k** |
| control: plain linear | $37.8k | $23.0k |

**Finding:** in this simulator, impact dominates risk, so the optimum is near-linear. The
old hand-tuned front-loading was costing about $3.5k per heat. AC ties the linear control
here. It is still the default because it *adapts*: in a more volatile real case, σ rises
and the same λ front-loads automatically, which a fixed curve cannot do. λ is in 1/$, so
recalibrate it in practice: `--grid execution.ac_risk_aversion=...`.

### Bertram optimal mean-reversion thresholds (ETF)

Bertram (2010), *Analytic solutions for optimal statistical arbitrage trading*. Fit an OU
process to the ETF premium and trade at ±a. Each hop earns 2a − cost and takes the mean
first-passage time E[T](a), so a* maximises (2a − cost) / E[T](a): the expected profit
**per unit time**, not per trade.

- E[T] is computed by numerical integration and verified against Monte Carlo (within 10%,
  `tests/test_research.py`).
- The cost is the live fees plus half-spreads of every leg.
- The premium is refitted every 10 ticks, and only when mean reversion is statistically
  significant. Until then, or when it isn't, the config edges are used.

Code: `pricing/timeseries.py` (`ou_passage_time`, `bertram_band`, `ou_continuous`),
`strategies/etf.py` (`thresholds`).

| Thresholds | Mean NLV | Worst seed | Stdev |
|---|---|---|---|
| hand-tuned `entry_edge 0.06 / exit_edge 0.01` | $32.8k | $10.4k | $14.4k |
| **Bertram (default)** | **$33.0k** | **$15.9k** | **$11.7k** |

**Finding:** Bertram matches a grid-searched setting with no tuning at all, and has a much
better tail. In the real case, where the premium's speed and volatility are unknown in
advance, a live fit beats any constant tuned on a simulator.

### Bayesian learning of news impact (commodity)

Model: price move = β × (−surprise) + ε, with a normal prior on β (`impact_per_unit`,
`impact_prior_sd`). Each inventory report's actual spot reaction, measured
`impact_learn_ticks` later, updates β in closed form (normal–normal conjugate). The
posterior sd shows how settled the estimate is.

Code: `strategies/commodity.py` (`BayesImpact`, `learn_impact`).

| Prior β | Fixed (never learns) | **Bayes (default)** |
|---|---|---|
| correct (0.25) | $409 | **$416** |
| wrong (0.10) | $313 | **$404** |

**Finding:** with a correct prior it costs nothing. With a wrong prior it recovers about
95% of the loss from mis-calibration within the heat. `impact_per_unit` is the
least-known number in the commodity brief, so this is the robust default.

### Per-option vol term structure (derivatives)

The case announces this week's realised vol and, mid-week, a range for next week. An option
expiring in 10 ticks lives entirely in this week; a 2-month option lives mostly in later weeks.
Each option's forecast is now `sqrt((a·σ_this² + (T−a)·σ_next²) / T)`, with `a` its ticks left in
this week. The old forecast moved one number halfway toward the range for every option.
16 seeds: +$26.4k (t 6.3) benign / +$26.0k (t 6.2) hostile; holdout +$30.6k / +$29.6k
(t 6.1, 16/16). Pricing the weeks after next at a 25% long-run vol instead lost $59k (t −8.6).

### Online crowd-impact learning for tenders (liability)

A crowded trade: every desk gets the same tender and unwinds it into the same book. The
bot learns the post-tender price move against the unwind, per 10k shares, with the same
conjugate normal update as the commodity news impact (`BayesImpact`), from a prior of
0.06 $/10k. It charges `crowd_weight` (1.5) × that × size in `evaluate_tender`, and shortens
the unwind to 12 ticks once the estimate is > 2 sd from 0. Final version: it learns only from tenders
it didn't take (its own unwind's impact is already priced) and charges nothing until the crowd
is significant. Hostile simulator: +$11.4k (t 3.9), holdout +$14.2k (t 3.0); benign −$0.3k (t −1.0)
and holdout $0. An ungated version with a 0.06 prior cost −$4.9k (t −3.0) in the benign market.
Details: [ARCHITECTURE.md](ARCHITECTURE.md).

## Evaluated, not adopted

| Technique | Test | Result |
|---|---|---|
| Fourier periodogram for return cycles | Fisher g-test on every case's returns | No significant cycle (p = 0.06–0.94). Nothing to forecast direction from. |
| Fourier volatility seasonality | Same test on \|returns\| | Real only for commodity: a 40-tick cycle, the scheduled reports, which the news feed already times exactly. |
| Fourier low-pass fair value | 1-step forecast vs random walk | **4.5–17.6× worse.** The periodic-extension assumption pulls forecasts back to the window start. Kalman ≈ random walk. |
| Malliavin–Mancino Fourier volatility | Bid-ask-bounced last-trade prices | 11% error vs 107% for naive realized vol. Useful only on trade-price history; the bots use mid prices (0% error). Candidate for the GARCH warm-up from `history()`. |
| Fourier (Carr–Madan / COS) option pricing | — | Only needed for Heston or jump models. RIT uses Black–Scholes, which has an exact closed form. |
| Whalley–Wilmott hedging band (derivatives) | No-trade band H = (3/2 · cost · Γ² / γ)^{1/3}, rebalance to the band edge; 8 seeds at γ = 1e-2 / 1e-3 / 1e-4 | Mean $18.2k / $19.6k / $22.6k vs $17.3k, but worst seed −$2.8k / −$2.8k / −$4.7k vs +$0.3k. Mean/stdev unchanged (1.15–1.17 vs 1.18). It's a wider band, so more risk for more return rather than a better trade-off. Removed. |
| Vega budget (derivatives) | Cap on \|portfolio vega\| at 100 contracts: $4k / $2.5k / $1.5k per vol point | Mean $17.1k / $9.2k / $4.7k vs $19.3k uncapped. A binding cap cuts exposure exactly when the edge is biggest. Size is controlled by `max_contracts = 60` instead. Removed. |
| End-of-period inventory skew (equity) | Avellaneda–Stoikov skew ramped up over the last 60 ticks | Mean $7.6k vs $8.1k, worse worst seed. The simulator has no drift, so there is little inventory risk to shed. Removed. |
| Queue-aware tender pricing (liability) | Price a tender behind inventory still being unwound | Worst seed better, mean lower on 5 of 6 seeds. Removed. |
| Copula portfolio optimisation | Kendall τ and tail dependence across tickers; estimator noise | Tickers are independent in the simulator, except futures vs spot, which is already hedged exactly. One heat (300 ticks) estimates tail dependence ± 0.10, about the size of the effect, so ~10 heats are needed. Positions are hedged arbs or seconds-long inventory capped by exchange limits. |
| Scenario stress test + CVaR position limit (derivatives): delta-hedged revaluation under a 5×5 spot × vol-shift grid (spot ±3σ√h, vol ±5 or ±10 pts, h = 75 ticks); each trade sized so CVaR₂₅ of scenario losses ≤ budget (Rockafellar–Uryasev) | Kill switch off in all rows. Limit $20k / $10k / $5k: mean −$1.6k / −$3.2k / −$5.1k (t −0.6 / −1.4 / −2.3); worst seed −$4.1k / −$3.0k / −$6.8k vs −$1.8k unlimited | Tighter limit = worse mean AND worse tail. Removed. The remaining losing seed isn't a vol-shock loss, and capping exposure blocks the trades with the largest edge. |
| Full Avellaneda–Stoikov (2008) quoting (equity) | γ ∈ {3e-6, 1e-5, 3e-5}, κ ∈ {20, 33}: −$1.3k to −$2.6k vs baseline (16 seeds, paired t −1.2 to −8.1) | The closed form's inventory term γσ²(T−t) shrinks to zero at the close, the opposite of what the end-of-heat penalty needs. The constant `skew_per_share` beat it on every setting. Removed. |
| VAMP (depth-weighted, 5,000 shares) and plain mid as fair value (equity) | +$0.1k to +$0.6k vs microprice, t ≤ 1.0 | Not significant. Book sizes in the simulator are random, so depth signals can only be judged in the RIT practice case. Not added. |
| Equity quote width, size and skew re-tuned under the official-rules simulator (2026-10-07) | 16 seeds: `vol_mult` 1.0/1.25/2.0, `size` 1000–3000, `skew_per_share` 0–1e-5 against 1.5 / 2000 / 3e-6 | Nothing beat the config. Bigger size: −$0.7k to −$2.3k (t −2.7 to −4.6); smaller size: −$1.0k to −$2.2k (t −4.4 to −7.0); stronger skew: −$0.8k to −$1.6k; no skew: −$1.7k with 3 losing seeds. The best row (skew 1.5e-6) was +$124, t 0.3, worse worst seed. Config unchanged. |
| ETF passive leg with a thin edge (`maker_edge` 0.02) | 16 seeds vs no passive leg | −$1.4k, t −2.1. The fills are thin and use up the risk room the taker arbs need. Wider edges win (kept at 0.15, see CHANGES.md). |
| Guéant–Lehalle–Fernandez-Tapia quoting / order-flow imbalance (equity) | — | Needs a realistic fill-intensity curve and order flow. The simulator has neither (fixed fill probability at the touch, random book rebuilds), so it can't be validated here. Equity gets the jump guard instead (`docs/ARCHITECTURE.md`). |

| Price band on aggressive orders (all cases, hostile sim) | Clamp every IOC limit to the 7-tick median mid ± (2 × median spread + 3 × EWMA tick move) | Commodity −$32 (t −3.5) in both markets; derivatives worst seed $33.7k → $24.4k; liability −$0.6k; ETF +$0.3k (t 1.3). It blocks real moves as often as fake ones. Removed. |
| Spoof-resistant books (all cases) | Cap each level at 3 × the median level size before every book calculation | ±$50 everywhere (equity hostile +$0.3k, t 1.2). Fake depth behind the touch barely enters walked-VWAP sizing. Removed. |
| Queue fighting vs penny-jumpers (equity) | Step back in front of a competitor, keeping 1–2 cents to the reservation price | −$2.6k / −$3.0k, t −2.8 / −3.0 (hostile). The fills won at a 1-cent edge are toxic. Removed. |
| Toxicity-adaptive spread (equity) | 5-tick markout of our passive fills (EWMA per side); widen the losing side by 1–2 × the loss, cap 5 cents | −$0.9k, t −0.9 (hostile). Removed. |
| Hold inventory on dislocation (equity) | Don't cross to cut inventory while the price is pushed > 1.5 bands against it | Never triggered: the size taper keeps inventory below `hard_inventory`. Removed. |
| Equity re-tune under the hostile simulator | `skew_per_share` 6e-6, `imbalance_lean` 0, `min_half_spread` 0.03, `vol_mult` 2.5, `size` 1000, `max_inventory` 10k, `jump_sigmas` 2.5, `jump_pause_ticks` 5, `ou_weight` 0 | Hostile: −$1.0k to +$0.4k, all \|t\| < 1.7. Skew 6e-6 cut the hostile worst seed −$5.2k → −$1.4k but cost −$0.8k (t −2.7) in the benign market. Config unchanged. |
| Equity kill switch under the hostile simulator | `max_drawdown` 2.5k / 6k / off vs 4k | 6k ≈ off: hostile +$0.4k (t 1.35), worst −$5.2k → −$2.0k; benign +$15 (t 1.5). The stop trips on manipulation drawdowns that revert ("stop hunting"). Not significant: unchanged, recheck in the practice case. |
| Equity re-tune under the queue model (2026-10-09, `--queue`, 32 seeds, benign / hostile) | `requote_tolerance` 0.009 / 0.0101 / 0.0201 / 0.0301; `min_half_spread` 0.01 / 0.015 / 0.03; `size` 1000 / 3000; `skew_per_share` 6e-6 / 1e-5; `jump_sigmas` 2.5 / 0 | Nothing beats the config. Tolerance and width: all \|t\| ≤ 1.7. Size 1000 / 3000: −$0.9k (t −4.9 / −4.1) benign, 3000 −$1.3k (t −6.8) hostile. Skew and jump guard: \|t\| < 1. Queue priority isn't what loses equity money; jumps through the quotes, trends and assigned blocks are. Config unchanged. |
| Requote tolerance float edge (equity, ETF maker) | `abs(old - new) < 0.01` is True for some 1-cent moves (15.03 − 15.02) and False for others (30.25 − 30.24), so 1-cent re-quotes happen by float noise. 0.009 (always) / 0.0101 (never) vs 0.01 | Equity queue model: −$52 / +$99 benign, +$107 / +$80 hostile, all \|t\| < 0.7. Harmless in practice; left as is. |
| ETF FX hedge at the USD touch (IOC limit) vs market orders | 32 seeds, `fx_hedge_band` 50k | Identical on every seed: the hedges fit inside the touch. Removed (the cost is the half-spread on USD turnover either way). |

## Liquidity Risk Case, 2027 selection brief (2026-10-10)

Keep rule: paired t >= 2 on seeds 1-16 (normal), no loss in `--hostile 1`, worst seed and CVaR not
worse, confirmed t >= 2 on holdout seeds 101-116; grids shortlist only rows with p adj < 0.05.
Stress knobs via `RITC_STRESS`; new knob `flow` scales how often passive flow reaches resting orders.

### Kept

| Change | Seeds 1-16 | Holdout 101-116 | Hostile | Notes |
|---|---|---|---|---|
| `freeze_rejected_bids` (safety): an auction bid stays in the window until expiry even when the server answers `success=false` at once; the bot traded the stock in that window | +$2.3k (t 2.3) | -$0.1k (t -0.3) | -$0.2k / +$0.6k | Strict front-running reading +$9.6k (t 3.0). Kept as a fine-safety fix (never trade in an undecided window), not for P&L. |
| Hold to the bell: `close_hold_ticks = 90` (never cross in the last 90 s) + `hold_valuation` (value late tenders at the free close-out) | +$7.0k (t 3.8, 16/16), worst $9.9k -> $13.8k | +$9.5k (t 4.6) | -$0.2k (t -0.2); holdout +$1.3k, worst -$6.5k -> +$2.2k | Scarce passive fills (flow 0.3 / 0.1): hold vs none +$8.6k (t 4.2 / 3.7). Valuation alone: +$3.5k (t 1.9), holdout +$7.2k (t 3.8). |
| `hold_risk_budget = 40000`: before the hold window, only rest while \|pos\| x GARCH sigma x sqrt(ticks left) <= $40k; cross only the excess | +$4.3k (t 2.9, p adj 0.044), worst $13.8k -> $24.0k, CVaR $21.3k -> $24.5k | +$5.0k (t 2.2); 2nd holdout 201-216 +$2.4k (t 2.8) | +$1.7k (t 0.9); 201-216 +$6.1k (t 2.1) | Hostile holdout 101-116 -$1.9k (t -0.6), worst $2.2k -> -$7.4k. "Everything at once" stress: worst -$84k -> -$105k (sd ~$95k). 20k: +$3.8k (t 2.9), holdout t 2.0. |

Why it works: the brief says prices are a random walk and the close-out at the last traded price
is free, so holding costs nothing on average while crossing always pays fee + spread (+ impact in the
sim). Never crossing at all (`close_hold_ticks = 420`) was +$11.7k (t 4.4) in the sim but ignores
volatility; the $ budget crosses more automatically when GARCH sigma is high.

### Tried, not kept (code removed or setting unchanged)

| Idea | Result | Verdict |
|---|---|---|
| `risk_aversion` 0 / 0.1 (before the hold) | normal +$8.7k (t 4.6), holdout +$6.8k (t 2.4), but hostile -$4.0k (t -2.8) / -$2.8k (t -3.0) | Fails hostile. Unchanged at 0.3. |
| `risk_aversion` 0 / 0.15 / 0.5 (after the hold) | 0: +$6.9k (t 3.1) but worst $13.8k -> $7.8k, CVaR down; 0.15: +$1.3k (t 0.7); 0.5: -$5.6k (t -3.4) | Fails the tail rule. Unchanged. |
| `min_profit_per_share` -0.01 / 0 / 0.02 / 0.03 | Before the hold: -0.01 +$3.7k (t 3.2) and 0 +$2.3k (t 3.0). After the hold: -0.01 +$1.9k (t 1.8), 0 +$0.9k (t 1.1), 0.02 -$1.8k to -$2.4k. Hostile: -$0.2k to -$1.8k | Not significant once holding. Unchanged. |
| `refill_factor` 1.5 / 3 / 4 | 3: normal +$3.4k to +$5.1k (t 2.0-2.8) but hostile -$3.5k to -$4.7k (t -2.6) | Fails hostile. Unchanged. |
| `competitive_margin` 0.02 / 0.03 / 0.08 | normal \|t\| < 1.7; hostile 0.02 / 0.03: -$3.8k / -$3.3k (t -2.9 / -2.6) | Unchanged at 0.05. |
| Live per-ticker depth (EWMA of depth within 10c, `depth_halflife` 5 / 20; refill x avg/current, clipped 0.5-2) | normal +$0.5k / +$1.1k (t 0.4 / 0.7); hostile -$1.9k / -$2.1k (t -2.4 / -2.5) | Liquidity vacuums inflate the ratio and the bot over-accepts. Removed. |
| Net-limit shadow price (potential function shadow x avg \|net/100k\|^k, credit when net moves to 0; shadow 0.05 / 0.10, k 1 / 3) | normal -$0.2k to -$2.5k (t -1.5 to -2.2); tenders x2 -$0.4k to -$2.2k; hostile \|t\| < 1.3. Credit-only: \|t\| < 0.6 normal, -$0.1k / -$0.8k hostile | Saving room costs more than the room is worth. Removed. |
| Skip auctions that add to a same-direction position being unwound (the bid freezes the stock until expiry): threshold 0 / 10k shares | -$0.8k (t -2.1 / -2.0); strict front-running -$0.9k | Removed. Bidding is worth more than the freeze. |
| `unwind_horizon_ticks` 20 / 45 / 60 | Before the budget: 20 -$1.7k (t -3.5), 45/60 +$1.2k/+$1.4k (t 1.9/1.8). After it: \|change\| < $50 | Unchanged at 30. |
| `display_qty` 2500 / 10000; `max_slippage` 0.02 / 0.08 | \|t\| < 1.9; max_slippage changes nothing in the normal sim (catch-up size is set by participation) | Unchanged. |
| `close_hold_ticks` 60 / 150 with the budget | -$0.6k (t -1.0) / +$3.2k (t 1.75), hostile -$0.6k / -$0.7k | Unchanged at 90. |
| Winner-take-all bid shading learned from wins / losses | Not implemented. Auctions + WTA are ~10 of ~35 tenders and ~$1k of ~$42k edge per heat (sim); ~5 WTA outcomes a heat are too few to learn from inside one heat. The sim's rival bid is independent of the book, so it cannot show the winner's curse either way. | Practice-server item. |

Note: `--queue` gives the same numbers as the normal market here. The sim's 4-cent spread makes the
executor step inside the spread, so it is always first in the queue.

### Crowd mitigations from docs/POSTMORTEM.md, validated on fresh seeds 201-232 (2026-10-10)

Implemented as flags, paired over 32 fresh seeds in 12 scenarios. Benign means base plus the 8 benign stresses;
"@a" means tenders priced off the visible mid (`RITC_STRESS=anchor=1`, new simulator option). Full table:
POSTMORTEM.md, "Validation on 201-232". All four failed and their code was removed; the `anchor` option stays.

| Idea | Target scenarios (diff vs config, worst seed) | Benign | Verdict |
|---|---|---|---|
| M1 crowd race on adverse arrival, incl. inside the bell (`crowd_race`, K = 12) | everything +$2.1k (t 0.3), worst -$43k → -$30k, losing 8 → 3; hostile -$2.5k (t -0.9); hostile@a -$0.1k, worst -$7.3k → -$9.3k; everything@a $0, worst -$38k → -$43k | \|t\| <= 1.3; 6 / 44 RACE lines in base / vol ×2 (false gates) | Removed. The gain is path dependence on the unanchored displacement artifact; anchored tails get worse. |
| M1 + M2 per-ticker crowd impact (tau 0.05) | everything +$6.8k (t 0.8), worst -$15k; hostile -$1.2k, worst -$1.2k → -$7.2k; hostile@a worst -$13.2k, losing 5 → 7 | \|t\| <= 1.3 | Removed |
| M2 alone | hostile +$2.7k (t 2.0) but worst -$9.9k; hostile@a -$0.4k, losing 5 → 9 | \|t\| <= 1.0 | Removed (anchored test failed) |
| M3 crowd position cap 50k (alone) | everything -$13.3k (t -2.0), worst unchanged; hostile -$2.7k | 0 | Removed |

The anchored losers lose to crowds that arrive before the crowd gate has evidence (RC4). The race starts after
the ramp and sells near the permanent level. The fix is operational: set `crowd_prior_mean` from the practice
rounds if they show a crowd.

### 2026-10-11: gap analysis (docs/POSTMORTEM.md)

Kept:

| Change | Evidence | Verdict |
|---|---|---|
| `decide_late_ticks = 3`: answer tenders 3 ticks before `expires`, re-priced then | fresh 301-316: base +$25.3k (t 4.5, 15/16), anchored hostile +$10.4k (t 4.2), real-world + hostile +$9.7k (t 3.8), losing heats over 21 scenarios 13 → 1, worst -$104.9k → -$2.5k; seeds 1-16 +$17.0k (t 3.1); holdout 101-116 +$22.0k (t 3.5); unanchored (flawed) hostile -$4.6k (t -0.9) | Kept. 2 and 4 similar; 4 left 4 losing heats on 301-316 |
| Safety for it: answer at once if `expires` is missing / > `decide_late_max_window` (30) ticks away / unreadable; never wait longer | unit test | Kept (no effect in the sim) |
| Auction: ignore a zero / absurd reference price | unit test | Kept (robustness) |
| `decide_late_end_guard`: never wait past the last tick a tender can still be accepted | found by a real-time HTTP test (3 of 32 tenders missed); seeds 1-16 +$3.7k (t 4.1), 301-316 +$2.3k (t 2.6), anchored hostile ±$0.1k | Kept |

Tried, not kept:

| Idea | Result | Verdict |
|---|---|---|
| `crowd_score_at = expiry` (score crowds from arrival to 10 ticks after the window closes) | late-crowd stress -$4.7k (losing 2 → 4); vol ×2 -$6.9k (t -2.7, false gates); arrival-crowd +$3.4k (t 1.8) | Removed |
| `hold_risk_budget` 20k / 80k with late answers | \|t\| < 2 everywhere (20k: -$0.5k to -$1.2k) | Unchanged at 40k |
| Crowd prior 0.05 / 0.10 (sd 0.03) with late answers | +$0.1k to +$1.6k, worst unchanged; base -$0.6k / -$1.4k | Unchanged at 0 |
| Fixed crowd charge 0.075 (prior sd 0.01, noise 1.0) | late crowd -$6.2k / +$9.6k; base -$41.5k (t -10.4) | Rejected |
| Head-start playbook (answer at once, 12-tick unwind, no hold) | late crowd: worst -$26.9k → -$9.8k / -$23.5k → +$2.3k, losing 2 → 1 / 5 → 0; base -$34.2k (t -7.5); arrival crowd -$14.2k (t -3.9) | Conditional only: practice evidence of a crowd at EXPIRY (POSTMORTEM.md) |

### 2026-10-11: volatility forecasting / hidden Markov regimes: value-of-information test

Question: would a better vol model (or an HMM on vol regimes) improve the bot? It already runs an online
GARCH(1,1) per stock (risk charge, hold budget, Almgren-Chriss speed, late valuation). Upper bound: give the
bot the TRUE current vol (`RITC_ORACLE_VOL=1 python -m ritc stress`), including in a new hidden 2-state vol
regime world (`vol_regime=2.5`: calm x0.5 / turbulent x2.5, switching 1/50 per tick).

| Scenario (16 seeds) | GARCH | Oracle vol | Oracle vol x0.5 | Oracle vol x2 |
|---|---|---|---|---|
| base, seeds 1-16 / 301-316 | 58,013 / 71,225 | 57,592 / 71,494 | - / 76,641 | - / 63,324 |
| vol regimes (hidden), 1-16 / 301-316 | 59,059 / 88,681 | 58,943 / 83,924 | - / 88,982 | - / 79,833 |
| volatility x2, 1-16 / 301-316 | 67,677 / 91,391 | 65,998 / 90,017 | | |
| hostile anchored, 1-16 / 301-316 | 14,477 / 12,824 | 14,248 / 12,440 | - / 13,935 | - / 12,736 |

Perfect vol knowledge adds nothing (-$4.8k to +$0.3k), even in the regime world built for it, so no vol
model or HMM can add more. A 2x-too-high estimate costs ~$8k (base), far beyond GARCH's real error. Not built.
Instead: the bot logs `VOL <stock> $x/tick` every 60 ticks and `calibrate` prints a vol-clustering Q per stock
(constant vol: Q 1.3-5.7; regimes: Q 29-30; 5% threshold 11.1) to check GARCH and regimes on real data.
Other regime candidates: liquidity (CROC) is observed directly in the book, not hidden (and live per-ticker
depth was rejected above); the crowd gate already is a 2-state detector; price-direction regimes don't exist
in a random walk and trading them would be fined as speculation.

### 2026-10-11: fine tuning with inventory-risk frontiers (Cartea & Jaimungal, Math. Finance 2015)

The paper (paywalled; worked from its known content) tunes a market maker by tracing expected P&L against
inventory-risk metrics as the inventory penalty varies. Its quoting model is not usable here (opening
positions is fined), but the fine-tuning method is. `ritc stress` now reports avg shares held, avg and
peak $ risk to the bell (sum |q| x sigma x sqrt(ticks left)) and `--sweep KEY=v1,v2,...` traces the frontier.
Seeds 401-432, 32 heats per row:

| `hold_risk_budget` | base | real-world | hostile | everything anchored (mean / worst) | crowd at expiry (mean / CVaR25) | base avg $risk |
|---|---|---|---|---|---|---|
| 0 | 61,399 | 55,129 | 34,837 | 27,819 / −3,033 | 17,422 / −22,348 | 5,390 |
| 20,000 | 65,606 | 57,181 | 34,336 | 27,636 / −19,922 | 19,668 / −17,715 | 6,412 |
| **40,000 (config)** | 65,086 | 57,006 | 34,475 | 27,325 / −19,922 | 20,263 / −16,173 | 6,706 |
| 80,000 | 64,959 | 57,019 | 34,475 | 27,180 / −19,922 | 21,493 / −14,890 | 6,764 |

| `close_hold_ticks` | base | real-world | everything anchored (mean / worst) | crowd at expiry (mean / worst / losing) | base avg $risk |
|---|---|---|---|---|---|
| 0 | 60,949 | 55,983 | 27,148 / −9,564 | 19,544 / −47,531 / 8 | 6,432 |
| 30 | 63,695 | 56,272 | 27,382 / −19,922 | 20,592 / −39,781 / 8 | 6,470 |
| **90 (config)** | 65,086 | 57,006 | 27,325 / −19,922 | 20,263 / −39,781 / 9 | 6,706 |
| 150 | 66,713 | 58,880 | 27,325 / −19,922 | 18,154 / −39,781 / 10 | 6,909 |
| 420 | 68,352 | 60,038 | 26,389 / −19,922 | 16,372 / −64,728 / 10 | 7,471 |

Verdict: the budget frontier is flat from 20k to 80k (the dial buys little); the bell window is a real
risk/return dial (about +$1.6k per extra minute in calm markets, but holding all heat takes the crowd tail
from -$39.8k to -$64.7k). 90 sits at the knee and no alternative passes the t >= 2 rule (150 earlier: t 1.75),
so the config is unchanged; the frontier became risk profiles in PRACTICE_PLAN.md. The paper's other
transferable idea, WHERE to rest the unwind (fill probability decaying with distance from the mid), needs real
fill data: `ritc record` now stores time and sales and `calibrate` estimates P(reach d) and kappa per stock.

### 2026-10-10: post-trade review of the eight liability logs ([POSTMORTEM.md](POSTMORTEM.md))

Every log was matched to its simulator seed and the current bot replayed on it; each tender decision was then
flipped and replayed (skill = every P&L part except inventory moves). Pooled over the five base-market seeds,
declined private tenders 10c+ inside the mid were worth +$6.3k a heat of skill (5/5), small-edge declines +$4.9k,
auction bids short of the reserve up to +$2.9k. Candidates, paired on the same seeds (scratch patches, no bot
code changed):

| Change | Seeds 1-32 (or 1-16) | Fresh / holdout | Hostile (anchored) | Crowd at expiry | Verdict |
|---|---|---|---|---|---|
| `refill_factor` 3 + `min_profit_per_share` 0 | +$4.5k (t 3.40) | 101-132: +$3.6k (t 2.99), worst unchanged | +$0.3k / +$1.3k (t 1.97) | -$3.5k (t -1.93), worst -$27k -> -$53k | candidate: gated on the practice crowd check |
| `refill_factor` 3 | +$3.7k (t 2.83) | +$2.1k (t 2.12) | -$0.3k / +$0.1k | -$1.5k, worst -$27k -> -$44k | weaker than the pair |
| `min_profit_per_share` 0 | +$1.4k (t 1.81) | +$2.6k (t 2.80) | +$0.6k / +$1.0k | -$1.4k / +$0.1k | fails design t >= 2 |
| Value the held part of private tenders at the mid (budget split) | +$3.5k (t 1.56) | | +$2.3k (t 1.82) | -$9.3k (t -2.69) | rejected unless no expiry crowd |
| Value every tender (incl. auctions) at the mid | +$2.1k (t 0.98) | | +$1.7k | -$13.2k (t -3.29) | rejected |
| Bid auctions off the mid | -$1.2k (t -1.36) | | -$0.5k (t -2.86) | -$3.7k | rejected |
| Valuation window 420 (valuation only) | +$2.0k (t 1.41) | | $0 | -$1.8k | not significant |
| Answer tenders in the last 5 s (`wind_down` -> `handle_tenders`, `min_ticks_to_unwind` 1) | +$0.4k (t 1.53) | 33-96: +$0.9k (t 3.14), worst unchanged | $0 / +$0.1k | +$0.4k | passes; not yet in the bot |
| Answer-deadline safety net (answer if the next loop would miss) | identical at normal speed (33-96, both markets) | | | | 4 ticks/loop +$9.8k (76% -> 99% answered), 5: +$17.8k; passes; not yet in the bot |
| Rest one tick behind the touch | +$0.2k (t 0.12), worst $20.0k -> -$4.9k | | +$1.2k | +$2.7k | rejected (tail) |
| Never step inside the spread | +$0.5k (t 0.52), worst $20.0k -> $5.6k | | +$0.3k | +$0.1k | rejected |

The earlier rejection of `refill_factor` 3 was measured in the unanchored hostile market (tenders priced off the
true mid, the artifact POSTMORTEM.md, risk review describes); in the anchored one it no longer loses. Slow loops
alone cost -$1.6k (2 ticks per loop), -$9.5k (3), -$22.2k (4), -$37.9k (5) even with every tender answered.

## Baselines and method results

**Per-case results, current code at the time (2026-10-07, lock-step, official rules; tuning seeds 1-16 and held-out
101-116).** Mean / worst over all 32 seeds: liability $45.8k / $23.5k (0 losing), ETF $30.6k / -$1.5k (1),
derivatives $112.5k / -$1.0k (1), equity $6.0k / -$1.2k (2), commodity $378 / $252 (0). Liability's held-out
seeds beat its tuning seeds (no sign of overfitting); equity's were worse (the clearest overfit). The
liability bot has moved on since (CHANGES.md); `ritc report --stress` gives its current numbers.

**Block execution vs always crossing** (liability, 8 seeds): block $49.7k (worst $22.6k, 8/8 wins) vs slice
$24.1k ($8.2k). Resting children earned 1.5-1.9c/share vs the arrival mid, crossing slices cost 1.8-3.0c.

**Common random numbers for passive fills** (equity, 16 seeds, paired SE): `imbalance_lean` $809 -> $234 (a
"significant" +$1.7k turned out to be fill luck), `jump_sigmas` $821 -> $137.

**What the hostile market costs** (16 seeds, before defences): liability $25.6k -> $8.6k (-$17.0k, t -3.9, the
crowd is -$33.5k of it without crowd learning), equity $6.8k -> $1.5k (penny-jumpers -$4.7k), derivatives and
commodity unchanged, ETF +$12.5k (other arbitrageurs leave 30% of each gap). Liquidity vacuums help resting bots.

**Kill switch calibration** (8 seeds): liability $8k stop -$2.3k, $15k no cost, now off; derivatives $40k stop
turned seed 11 from +$40.3k into -$14.0k, $80k never trips; equity $4k stop hunted (off: +$951, t 3.0); ETF off
(a hedged convergence trade is worst exactly when the edge is widest).

## Reproduce

```bash
python -m ritc tune liability   --seeds 8 --grid execution.schedule=front_load,almgren_chriss
python -m ritc tune etf         --seeds 8 --grid strategy.threshold_mode=fixed,bertram
python -m ritc tune commodity   --seeds 8 --grid strategy.impact_mode=fixed,bayes --grid strategy.impact_per_unit=0.10,0.25
python -m ritc tune liability   --hostile 1 --grid strategy.crowd_learn=false,true
```
