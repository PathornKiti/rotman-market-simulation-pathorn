# RITC Liquidity Bot

Algorithmic trading bot for the **Rotman International Trading Competition (RITC)** Liquidity Risk Case
(institutional tender offers), running on the Rotman Interactive Trader (RIT) REST API.

**Core idea:** price each tender offer against the cost of unwinding it into the book. Accept only when
profit/share clears fees, slippage and adverse drift, then unwind in book-sized slices. Optional profiles hold a
block while the short-term trend runs our way (RSI / momentum, or the trained ML price model).

| Profile | Idea |
|---|---|
| `A_default` | Adaptive front-load plus the ML sprint model. The safe choice in crowded or bot-heavy rounds. |
| `rsi_bot` | Accept every tender priced better than the market and ride the 10-tick trend. |
| `rsi_ml` | `rsi_bot` with the ML price model as the trend signal. |

**How to install and run it on macOS and Windows: [docs/MANUAL.md](docs/MANUAL.md).**

An **offline simulator** serves the same REST API, so the bot can be rehearsed without the Windows-only RIT client:

```bash
ritc sim liquidity            # terminal 1
ritc run liquidity --live -v  # terminal 2 ("live" against the simulator)
```

## Docs

| | |
|---|---|
| [MANUAL](docs/MANUAL.md) | install, run, review (macOS and Windows) |
| [strategies/liquidity](docs/strategies/liquidity.md) | the bot, in detail |
| [RSI_STRATEGY](docs/RSI_STRATEGY.md) · [RSI_TRADING_FLOW](docs/RSI_TRADING_FLOW.md) | the trend-hold logic |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | code, execution, risk controls, simulator, journal |
| [OFFICIAL_RULES](docs/OFFICIAL_RULES.md) · [ASSUMPTIONS](docs/ASSUMPTIONS.md) | the case brief and what we assumed |
| [RESEARCH](docs/RESEARCH.md) · [CHANGES](docs/CHANGES.md) · [PRACTICE_LOGS](docs/PRACTICE_LOGS.md) · [POSTMORTEM](docs/POSTMORTEM.md) | experiments with numbers |
| `docs/results/` | ML models (`ml_*`) and RSI / trend-hold results (`rsi_*`) |

## Layout

```
config/liquidity.toml            tickers, fees, order sizes, risk limits, thresholds
config/liquidity_profiles.toml   A_default, rsi_bot, rsi_ml
src/ritc/
  cli.py                         python -m ritc {list,doctor,monitor,analyze,tune,sim,run,...}
  core/                          client, order book, execution, block algo, feed, risk, runner, journal
  pricing/                       EWMA / rolling stats, GARCH / OU time series
  strategies/liquidity.py        the bot: pure decision functions + a thin Strategy class
  mlmodel.py                     tree price model used by the ML sprint / rsi_ml
  sim/server.py                  offline RIT simulator
  postmortem/, stress.py, ...    reports, stress scenarios, practice scoreboard
tests/                           unit tests + end-to-end simulator runs
docs/                            manual, architecture, rules, research, results
```

## Safety features

Dry run by default (only `--live` sends orders); protective limits instead of market orders; immediate-or-cancel
sweep of any unfilled remainder; sizes taken from the book; pre-trade gross/net limit check; clean shutdown that
cancels every resting order; drawdown kill switch with a graduated throttle; TCA report at shutdown.

## Development

```bash
python -m pytest -q
python -m ruff check src tests
python -m ritc tune liquidity --grid strategy.min_profit_per_share=0.03,0.05 --seeds 8
```

> The simulator checks **logic and plumbing**, not the score. `--hostile 1` adds manipulative competitors and
> crowded tenders; `--queue` adds price-time queue priority. Judge any change on real RIT heats first.
