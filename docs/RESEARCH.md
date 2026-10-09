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
Details: [HOSTILE_MARKET.md](HOSTILE_MARKET.md).

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
| Guéant–Lehalle–Fernandez-Tapia quoting / order-flow imbalance (equity) | — | Needs a realistic fill-intensity curve and order flow. The simulator has neither (fixed fill probability at the touch, random book rebuilds), so it can't be validated here. Equity gets the jump guard instead (`docs/RISK.md`). |

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

## Reproduce

```bash
python -m ritc tune liability   --seeds 8 --grid execution.schedule=front_load,almgren_chriss
python -m ritc tune etf         --seeds 8 --grid strategy.threshold_mode=fixed,bertram
python -m ritc tune commodity   --seeds 8 --grid strategy.impact_mode=fixed,bayes --grid strategy.impact_per_unit=0.10,0.25
python -m ritc tune liability   --hostile 1 --grid strategy.crowd_learn=false,true
```
