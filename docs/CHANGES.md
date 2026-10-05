# Summary of changes: what was added, what was removed

Every change below was measured on the simulator before being kept. The rule was
simple: **a change stays only if it improved results.** Anything that didn't was
removed from the code, and its measurements are kept in [RESEARCH.md](RESEARCH.md).

Numbers are mean final NLV across 8 seeds, with the worst seed in brackets, on the
reproducible lock-step simulator unless noted. Simulator P&L is noise-driven, so read
them as direction, not as a forecast.

## Final results per case

| Case | Starting point | Final |
|---|---|---|
| Liability | +$47.3k¹ / lock-step $31.6k (worst $16.9k) | **$37.6k (worst $22.5k)** |
| Derivatives | **−$20.3k**¹ | **$17.3k (worst +$0.3k)** |
| ETF | **−$0.5k**¹ | **$33.0k (worst $15.9k)** |
| Equity | +$1.3k¹ / lock-step $7.6k (worst $3.2k) | **$8.1k (worst $5.9k)** |
| Commodity | +$291¹ (worst −$244) | **$416 (worst $343)** |

¹ Measured on the old real-time simulator (6 seeds), before it was made reproducible. That
simulator's timing noise made results vary by up to $10k between identical runs, so only
the large moves (derivatives and ETF going from losing to winning) are meaningful from it.

## Added: kept because they improved results

### Infrastructure (all cases)

| Change | Why it matters |
|---|---|
| **Reproducible simulator.** Separate random generators for prices and fills, plus lock-step mode for `ritc tune`. | Same seed = same market for every setting compared. Before, equity runs varied by up to $10k from thread timing alone, so A/B tests were noise. |
| **News fetched with every price snapshot** | The bot never trades a repriced market against a stale headline. This was a major derivatives loss. |
| **Fill-based bookkeeping** (`Executor.filled`) | Hedges and strategy books count what filled, not what was sent. |
| **Package risk room** (`RiskManager.room_package`) | Multi-leg trades are sized against shared gross/net limits. Per-ticker checks let an ETF + basket trade use 3× the limit we thought. |

### Risk management (all cases)

| Change | Measured effect |
|---|---|
| **Graduated drawdown throttle**: new-risk sizes shrink from 50% of `max_drawdown`, kill switch at 100%; exits and hedges always run at full size | Losing streaks get smaller bets instead of an all-or-nothing stop |
| **Kill-switch levels** at ~1.5–2× the worst normal drawdown: liability $15k, derivatives $40k, equity $4k, commodity $200; ETF off on purpose | Close to free in normal runs. Set tighter, it hurt badly (derivatives $20k turned the worst seed from +$0.3k to −$14.7k). |

### Per case

| Case | Change | Measured effect |
|---|---|---|
| Liability | **Almgren–Chriss optimal execution** (λ = 3e-7, σ from GARCH, impact from book depth) | $34.1k → $37.6k, worst $16.9k → $22.5k |
| Liability | `min_profit_per_share` 0.03 → 0.01 (raised automatically in a drawdown) | $31.6k → $34.1k, same worst seed |
| Liability | Rejected competitive bids no longer counted as positions | Risk room stays free for the next tender |
| Derivatives | **Hold positions while the edge remains** (no trimming as IV converges) | Biggest single fix. Trimming was ~7,000 contracts of spread cost per run. |
| Derivatives | Delta hedge from **fills**, counting **every held option** (incl. near expiry) | Stopped ±6–8k share hedge flip-flops |
| Derivatives | Parity trades bounded by package risk room | No unbounded repeats |
| | *Derivatives combined:* | **−$20.3k → +$21.5k** (old simulator) |
| ETF | **Executable exit**: close when the reverse arb pays after costs, not when the mid premium hits zero | The old exit paid a full second set of costs; −$0.5k → +$16.8k (old simulator) |
| ETF | Complete a missed ETF leg while the premium still favours it; slippage budget from surplus edge | Fewer leg repairs at a loss |
| ETF | **Bertram optimal OU thresholds**, fitted live | Same mean as hand-tuned with no tuning; worst seed $10.4k → $15.9k |
| Equity | **Jump guard**: pull quotes for 2 ticks after a move > 4σ | $7.6k → $8.1k, worst $3.2k → $5.9k |
| Commodity | Carry book tracked from fills; spot re-hedged every loop; spot unwound when a future expires | Worst seed −$244 → +$309; no naked spot leg |
| Commodity | **News stop-loss** + `news_max_size` 40 → 60 | $368 → $409, better on 7 of 8 seeds |
| Commodity | **Bayesian learning of news impact** | Right prior: $409 → $416; wrong prior: $313 → $404 |

## Removed: tried, measured, didn't improve

| Change | Case | Result | Why removed |
|---|---|---|---|
| Whalley–Wilmott hedge band | Derivatives | Mean up to +$5.3k, but worst seed +$0.3k → −$4.7k | Same mean/stdev (1.16 vs 1.18): more risk, not a better trade-off |
| Vega cap (`max_vega`) | Derivatives | Mean $19.3k → $17.1k / $9.2k / $4.7k as the cap tightened | Cuts exposure exactly when the edge is largest |
| End-of-period inventory skew | Equity | $8.1k → $7.6k, worse worst seed | No inventory drift risk to remove in this market |
| Queue-aware tender pricing | Liability | Lower mean on 5 of 6 seeds | Declined profitable stacked tenders |

## Evaluated, never added

| Technique | Finding |
|---|---|
| Fourier periodogram (cycles) | No significant cycle in returns (p = 0.06–0.94). The only real one, commodity volatility every 40 ticks, is the scheduled report, which the news feed already times. |
| Fourier low-pass fair value | 4.5–17.6× worse than a random walk |
| Malliavin–Mancino Fourier volatility | Accurate on noisy trade prices (11% vs 107% error), but the bots use mid prices (0% error). Possible future use: the GARCH warm-up from price history. |
| Fourier (FFT/COS) option pricing | RIT uses Black–Scholes, which has an exact closed form |
| Copula portfolio optimisation | Simulator tickers are independent; one heat is too short to estimate tail dependence (±0.10 error); positions are hedged arbs or short-lived inventory |
| GLFT optimal quoting / order-flow imbalance | Needs realistic fill and order-flow data the simulator doesn't produce |
| Larger sizes: ETF `clip`, commodity `carry_clip`, derivatives 100 contracts | Lower mean, or a higher mean with a worse worst seed |

## Before the competition

1. Rescale the kill-switch levels (`[run] max_drawdown`) to ~1.5–2× the worst drawdown
   seen in the RIT practice case. Keep ETF at 0.
2. Recalibrate `ac_risk_aversion` (liability). It's in 1/$, so it depends on the case's scale:
   `python -m ritc tune liability --grid execution.ac_risk_aversion=...`
3. Bertram (ETF) and Bayesian impact (commodity) re-fit themselves live. Check their log
   lines (`BERTRAM`, `IMPACT`) in practice.
