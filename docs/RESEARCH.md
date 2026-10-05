# Research-grade models: what was tried and what earned its place

A technique is adopted only if it beats the current, already-tuned setting on the
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

## Implemented, opt-in

### Whalley–Wilmott hedging band (derivatives)

Whalley & Wilmott (1997). The optimal no-trade band around the delta hedge under
transaction costs is H = (3/2 · cost · Γ² / γ)^{1/3}, using the portfolio gamma Γ. You
rebalance to the **edge** of the band, not to zero. The band is capped at 90% of the
fined delta limit.

Config: `hedge_mode = "whalley_wilmott"`, `ww_risk_aversion`.

| Hedge | Mean NLV | Worst seed | Mean / stdev |
|---|---|---|---|
| fixed band 15% of limit, to zero (default) | $17.3k | +$0.3k | 1.18 |
| WW γ = 1e-2 | $18.2k | −$2.8k | 1.15 |
| WW γ = 1e-3 | $19.6k | −$2.8k | 1.17 |
| WW γ = 1e-4 | $22.6k | −$4.7k | 1.16 |

**Finding:** WW raises the mean (by up to $5.3k), but by carrying more delta, so the tail
grows with it. Mean/stdev is unchanged. It's a leverage dial, not a better trade-off, so
it stays opt-in. Turn it on (γ ≈ 1e-3) if you want more return for more risk.

## Evaluated, not adopted

| Technique | Test | Result |
|---|---|---|
| Fourier periodogram for return cycles | Fisher g-test on every case's returns | No significant cycle (p = 0.06–0.94). Nothing to forecast direction from. |
| Fourier volatility seasonality | Same test on \|returns\| | Real only for commodity: a 40-tick cycle, the scheduled reports, which the news feed already times exactly. |
| Fourier low-pass fair value | 1-step forecast vs random walk | **4.5–17.6× worse.** The periodic-extension assumption pulls forecasts back to the window start. Kalman ≈ random walk. |
| Malliavin–Mancino Fourier volatility | Bid-ask-bounced last-trade prices | 11% error vs 107% for naive realized vol. Useful only on trade-price history; the bots use mid prices (0% error). Candidate for the GARCH warm-up from `history()`. |
| Fourier (Carr–Madan / COS) option pricing | — | Only needed for Heston or jump models. RIT uses Black–Scholes, which has an exact closed form. |
| Copula portfolio optimisation | Kendall τ and tail dependence across tickers; estimator noise | Tickers are independent in the simulator, except futures vs spot, which is already hedged exactly. One heat (300 ticks) estimates tail dependence ± 0.10, about the size of the effect, so ~10 heats are needed. Positions are hedged arbs or seconds-long inventory capped by exchange limits. |
| Guéant–Lehalle–Fernandez-Tapia quoting / order-flow imbalance (equity) | — | Needs a realistic fill-intensity curve and order flow. The simulator has neither (fixed fill probability at the touch, random book rebuilds), so it can't be validated here. Equity gets the jump guard instead (`docs/RISK.md`). |

## Reproduce

```bash
python -m ritc tune liability   --seeds 8 --grid execution.schedule=front_load,almgren_chriss
python -m ritc tune etf         --seeds 8 --grid strategy.threshold_mode=fixed,bertram
python -m ritc tune commodity   --seeds 8 --grid strategy.impact_mode=fixed,bayes --grid strategy.impact_per_unit=0.10,0.25
python -m ritc tune derivatives --seeds 8 --grid strategy.hedge_mode=fixed,whalley_wilmott
```
