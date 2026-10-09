# Test results: every case, current code (2026-10-07)

All five bots were re-run on the current code and configs, in one session, on the lock-step
simulator under the official RITC rules ([OFFICIAL_RULES.md](OFFICIAL_RULES.md)). Each case
ran on the tuning seeds 1–16 and on the held-out seeds 101–116, which no setting was tuned on.
Scores are final NLV in CAD. Derivatives is scored NLV − delta-limit penalty, the way the judges rank it.

Reproduce: `python -m ritc tune <case>` and `python -m ritc tune <case> --first-seed 101`.

Code checks: **105 tests pass** (`pytest -q`), **ruff clean**.

## Summary

| Case | Seeds | Mean | Median | Worst | CVaR25 | Stdev | Losing seeds |
|---|---|---|---|---|---|---|---|
| Liability | 1–16 | $42.1k | | $23.5k | $26.5k | $11.3k | 0 |
| | 101–116 | $49.4k | | $29.4k | $33.7k | $13.4k | 0 |
| | **all 32** | **$45.8k** | $44.3k | **$23.5k** | $30.0k | | **0** |
| ETF | 1–16 | $35.2k | | $21.7k | $24.9k | $9.0k | 0 |
| | 101–116 | $25.9k | | −$1.5k | $8.0k | $13.6k | 1 |
| | **all 32** | **$30.6k** | $30.0k | **−$1.5k** | $15.1k | | **1** |
| Derivatives | 1–16 | $117.4k | | $32.8k | $49.8k | $57.2k | 0 |
| | 101–116 | $107.5k | | −$1.0k | $46.5k | $47.3k | 1 |
| | **all 32** | **$112.5k** | $116.2k | **−$1.0k** | $47.4k | | **1** |
| Equity | 1–16 | $6.8k | | $3.5k | $4.9k | $1.7k | 0 |
| | 101–116 | $5.2k | | −$1.2k | $1.2k | $3.0k | 2 |
| | **all 32** | **$6.0k** | $6.2k | **−$1.2k** | $2.7k | | **2** |
| Commodity | 1–16 | $393 | | $260 | $312 | $59 | 0 |
| | 101–116 | $363 | | $252 | $286 | $66 | 0 |
| | **all 32** | **$378** | $380 | **$252** | $293 | | **0** |

CVaR25 is the mean of the worst quarter of seeds. The "all 32" CVaR25 uses the worst 8 seeds.

## Per seed

| Case | Seeds 1–16 | Seeds 101–116 |
|---|---|---|
| Liability | 45.4, 43.2, 23.5, 54.1, 28.0, 48.8, 25.7, 41.1, 34.4, 59.7, 42.9, 42.2, 50.2, 28.8, 57.3, 48.6 | 65.5, 55.9, 63.8, 35.3, 29.4, 53.3, 35.0, 35.1, 43.2, 38.0, 57.2, 68.8, 51.5, 68.4, 53.7, 37.2 |
| ETF | 35.8, 51.2, 29.5, 41.3, 21.7, 36.7, 39.0, 29.3, 26.0, 45.2, 31.2, 23.6, 28.4, 39.7, 33.6, 51.5 | 7.5, 33.4, 20.2, 18.5, 26.7, 49.7, 28.2, 30.4, 44.5, 24.9, 41.1, 28.1, **−1.5**, 23.4, 32.2, 7.6 |
| Derivatives | 89.5, 35.8, 32.8, 141.1, 117.0, 120.0, 222.5, 57.4, 116.7, 115.8, 198.1, 82.6, 122.9, 212.6, 140.5, 73.3 | 112.4, 119.7, 19.0, 188.8, 109.5, 129.9, 88.3, 135.8, 161.3, 110.1, 79.7, **−1.0**, 96.2, 99.4, 137.8, 133.1 |
| Equity | 6.8, 7.6, 6.7, 6.3, 10.2, 7.3, 7.5, 5.7, 8.8, 4.8, 6.7, 3.5, 5.8, 5.5, 6.1, 9.8 | 8.6, 7.7, 8.3, **−0.2**, 3.0, 9.9, 7.0, 4.5, 6.6, 5.8, 5.4, 4.0, 4.5, 5.8, **−1.2**, 3.1 |
| Commodity ($) | 426, 447, 407, 432, 343, 415, 451, 408, 278, 366, 368, 423, 377, 260, 437, 457 | 284, 350, 344, 409, 452, 383, 333, 487, 309, 334, 298, 430, 252, 365, 445, 332 |

$k unless marked. Losing seeds in bold.

## Reading the results

- **Liability** is the most consistent case: no losing seed in 32, and the held-out seeds are better
  than the tuning seeds. That's a sign the settings aren't overfit.
- **ETF** ($30.6k over 32 seeds) includes the 2026-10-07 tender offers and the passive ETF leg. Both
  were confirmed on 101–116 against the previous code ($21.4k → $25.9k there). The held-out set is
  harder than 1–16: one losing seed (113, −$1.5k) and two near $7.5k (101, 116). That's the next ETF item.
- **Derivatives** is the largest and the most variable ($19k–$223k). Most of it comes from reading the
  mid-week volatility forecast. In the simulator that forecast always contains next week's true vol,
  so treat the level as optimistic until the RIT practice case confirms it. Seed 112 (−$1.0k) is the only loss.
- **Equity** drops on the held-out seeds ($6.8k → $5.2k) and has 2 small losing seeds (104, 115). This
  is the clearest sign of overfitting to seeds 1–16 among the five cases. The 2026-10-07 re-tune found no
  better setting (RESEARCH.md), so the next step is to look at seeds 104 and 115, not more parameter grids.
- **Commodity** is small and stable. The official BP Commodities case disables API orders, so this bot
  models a different, generic case (OFFICIAL_RULES.md).

## Caveats

- The simulator's other traders are noise, not competing teams. Use these numbers to compare versions
  of the bots, not to predict a heat score.
- The ETF tender sizes and prices in the simulator are a guess (the case packages don't give them).
- Heats are ranked, so the worst seed and the losing-seed count matter as much as the mean.
