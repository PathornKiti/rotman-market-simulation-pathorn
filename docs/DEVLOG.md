# Development log and plan

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
4. **Vol forecast news bug:** the simulator's "next week" range is centred on *this*
   week's vol, so range-based forecasting can't be tested. Draw next week's vol in advance
   and announce that, as the real case does.
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
