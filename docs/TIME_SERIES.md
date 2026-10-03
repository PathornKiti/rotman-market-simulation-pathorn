# Time-series models: volatility and value through time

**Code:** `src/ritc/pricing/timeseries.py` (stdlib only) · **Tests:** `tests/test_timeseries.py`

Prices are a time series. Their recent path says something about the next tick's
**volatility** (GARCH) and sometimes about the next tick's **value** (Kalman level,
Ornstein-Uhlenbeck mean reversion).

## 1. GARCH(1,1): volatility through time

```
sigma2[t+1] = omega + alpha * r[t]^2 + beta * sigma2[t]
long-run variance  VL = omega / (1 - alpha - beta)
k-step forecast    E[sigma2[t+k]] = VL + (alpha+beta)^(k-1) * (sigma2[t+1] - VL)
term vol over h    sqrt( mean of the k = 1..h forecasts )          # closed form in term_vol()
```

- **Fit:** maximum likelihood with *variance targeting*. Omega is fixed so that VL equals
  the sample variance, leaving only alpha and beta to fit with Nelder-Mead. The parameters
  are transformed so every candidate is valid (alpha, beta ≥ 0 and alpha + beta < 0.999).
  This is stable on the few hundred ticks a RIT case provides, and recovers
  α=0.10/β=0.85 from 2,000 simulated returns as α≈0.10/β≈0.87.
- **Online use (`OnlineGarch`):** uses an EWMA (RiskMetrics, λ=0.94) until 60 returns
  are available. After that it refits on a rolling window every 50 ticks and updates the
  variance on every tick in between.
- **Horizon:** `term_vol(h)` forecasts volatility over a specific horizon, for example the
  ticks left until an option expires.

## 2. Value through time

| Model | Question it answers | Used for |
|---|---|---|
| `KalmanLevel` | "What is the price once bid/ask bounce and noise are removed?" Its noise settings are estimated from the data. | Denoised level used as the base fair value. |
| `fit_ou` → `OUFit` | "Does the price revert to a mean, and how fast?" Fitted as AR(1): long-run mean, half-life, z-score, `expected(x, h)`. | Leaning quotes toward the expected price h ticks ahead. |
| `FairValueModel` | Kalman level + OU projection, with the OU part used **only when significant**. | Equity bot `fair_shift`. |

**Significance matters.** Most RIT stocks behave like random walks, and an AR(1) fit
always finds *some* b < 1. The OU signal is used only when the t-statistic on (b − 1)
is below **−3.5**, roughly the 1% Dickey-Fuller critical value. The model refits every
25 ticks, so a 5% test would regularly report false mean reversion. Measured over 1,000
paths: **1.0% false positives** on random walks, and **100% detection** of a half-life-8
reverting series (300 ticks).

## 3. Where each bot uses them, and measured results

All results are from the offline simulator. They compare the same bot with the feature
on and off, on the same seeds.

| Bot | Feature | Result | Default |
|---|---|---|---|
| liability | Risk premium = `risk_aversion × GARCH price vol × √(unwind ticks)`, so a volatile market needs a fatter tender edge | +$3.0k average across 5 seeds (3 wins, 2 small losses) | **on** (`risk_aversion = 0.3`) |
| derivatives | Vol forecast = blend of news and GARCH term vol over each option's remaining life | Blending 30% GARCH **lost** on 5 of 6 seeds. The news reports this week's vol exactly; GARCH lags regime changes. | news first (`garch_weight = 0`). GARCH is used before the first vol headline. |
| equity | GARCH per-tick vol sets the half-spread | **Lost** to EWMA on 5 of 5 seeds. The simulator has **no volatility clustering** (`analyze` confirms it), so GARCH only over-reacts to jumps. | `vol_model = "ewma"`. Switch to `"garch"` if `analyze` shows clustering. |
| equity | OU `fair_shift` (capped at one half-spread) | Inactive in the simulator (random walks, correctly not significant) | on; it only acts when significant |

The simulator can't validate GARCH because its volatility doesn't cluster, and I
deliberately did not add clustering to the simulator to make the model look good.
**Decide with the real data:**

```bash
python -m ritc analyze            # in the official RIT practice case
```

```
ticker        n  vol/tick  LB Q(r^2)  clusters   alpha    beta    OU t  half-life  mean-rev
SPNG        235   0.00194        0.1        no   0.000   0.000   -2.01       21.3        no
```

- `clusters = YES` (Ljung-Box on squared returns above the 5% critical value):
  set `vol_model = "garch"` for equity. A fitted beta near 0.8–0.95 confirms it.
- `mean-rev = YES`: the equity bot already leans toward the OU forecast automatically.
  Consider a stat-arb style overlay.
