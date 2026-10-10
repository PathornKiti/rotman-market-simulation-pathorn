# RITC Trading Bots

Algorithmic trading bots for the **Rotman International Trading Competition (RITC)**.
They run on the Rotman Interactive Trader (RIT) REST API, with one strategy for each
case family:

| Case family | Bot | Core idea | Docs |
|---|---|---|---|
| **Liability trading** | `liability` | Price each tender offer against the cost of unwinding it into the book. Accept only when profit/share clears fees, slippage and adverse drift, then unwind in book-sized slices. | [docs](docs/strategies/liability.md) |
| **Derivatives trading** | `derivatives` | Volatility arbitrage. Buy options when implied vol is below the vol forecast from the news, sell when above. Stay delta-hedged inside the delta limit and pick up put-call parity breaks. | [docs](docs/strategies/derivatives.md) |
| **ETF trading** | `etf` | ETF vs basket arbitrage, sized by walking every leg's book. Missed legs are completed or repaired, and positions close when the reverse arb pays after costs. Supports an FX leg. | [docs](docs/strategies/etf.md) |
| **Equity trading** | `equity` | Market making that manages inventory (Avellaneda-Stoikov style). Quotes are centred on the microprice, spread widens with volatility, and quotes skew to reduce inventory. | [docs](docs/strategies/equity.md) |
| **Commodity trading** | `commodity` | Spot vs futures cost-of-carry arbitrage, plus short-term momentum trades on inventory news (bigger build than expected → short, bigger draw → long). | [docs](docs/strategies/commodity.md) |

All five share one tested core: API client, order-book maths, execution, risk limits
and the run loop. **Time-series models** (GARCH(1,1) volatility, a Kalman fair-value
level and Ornstein-Uhlenbeck mean reversion) feed the bots where they measurably help.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Speed and execution:** a real-time news/tender feed (~100 ms), parallel market-data
fetches, and concurrent multi-leg orders. Block trades are worked with a
passive-then-aggressive iceberg algorithm, which beat an always-aggressive unwind on
8 of 8 simulator seeds. Every fill is measured against arrival price (TCA), a drawdown
kill switch with a graduated throttle guards each heat, and every case has its own risk guards
([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). `python -m ritc tune` A/B-tests settings across seeds on a
**lock-step** simulator, so the same seed is the same market for every setting.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Research-grade models**, each adopted only after beating the tuned baseline on the
reproducible simulator: Almgren–Chriss optimal execution (liability), Bertram optimal
mean-reversion thresholds (ETF), and Bayesian news-impact learning (commodity). Whalley–Wilmott hedging, vega caps, Fourier
and copula methods were evaluated and removed because they didn't improve results. See [docs/RESEARCH.md](docs/RESEARCH.md) for the evidence. [docs/CHANGES.md](docs/CHANGES.md) summarises everything added
and removed, with measured results.

An **offline simulator** serves the same REST API, so you can rehearse every case
without the Windows-only RIT client.

---

## Quick start

```bash
pip install -e ".[dev]"        # or: pip install -r requirements.txt
cp .env.example .env           # paste your RIT API key into RIT_API_KEY

python -m ritc doctor          # check the connection, dump raw API data
python -m ritc run equity      # DRY RUN: logs decisions, sends nothing
python -m ritc run equity --live
```

### Rehearse offline (no RIT client needed)

```bash
# terminal 1 - simulated market on localhost:9999
python -m ritc sim liability

# terminal 2
python -m ritc run liability --live -v     # "live" against the simulator
python -m ritc monitor                     # optional read-only dashboard
```

Swap `liability` for `derivatives`, `etf`, `equity` or `commodity`.

### After a heat: reports (liability)

```bash
pip install -e ".[report]"     # openpyxl, for the .xlsx (the .html needs nothing)
python -m ritc report          # every logs/liability-*.log -> reports/
python -m ritc report --stress # the current bot through every stress scenario, every seed (simulator)
```

`ritc run` writes a run journal (`logs/*.jsonl`: every tick's NLV, positions and quotes; every order, tender,
answer, error and slow loop) next to each log. Each run gets `reports/liability-<date>-<time>_live.html` (RIT
server) or `_local.html` (simulator) and a matching `.xlsx` laid out like the case's `Liquidity help file.xlsx`:
P&L path, where it came from, the distribution of what the bot struggled with, gap checks against the brief, and
for local runs the current bot replayed on the same market. `reports/live-learnings.json` keeps every run's
numbers for the next review. Details: [docs/POSTMORTEM.md](docs/POSTMORTEM.md).

### Docs

| | |
|---|---|
| [RUNBOOK](docs/RUNBOOK.md) | install, set up a real case, competition day, after the heat |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | code, execution, risk controls, models, simulator, fair testing, journal and reports |
| [OFFICIAL_RULES](docs/OFFICIAL_RULES.md) | what the official case packages say |
| [ASSUMPTIONS](docs/ASSUMPTIONS.md) · [PRACTICE_PLAN](docs/PRACTICE_PLAN.md) · [POSTMORTEM](docs/POSTMORTEM.md) | liability: assumptions, practice-day plan, reviews and reports |
| [RESEARCH](docs/RESEARCH.md) · [CHANGES](docs/CHANGES.md) | every experiment with numbers; what changed when, open items |
| [strategies/](docs/strategies/) | one page per bot |

## Repository layout

```
.
├── config/                     # ONE FILE PER CASE: edit to match the case brief on the day
│   ├── liability.toml          #   tickers, fees, order sizes, risk limits, thresholds
│   ├── derivatives.toml
│   ├── etf.toml
│   ├── equity.toml
│   └── commodity.toml
├── src/ritc/
│   ├── cli.py                  # python -m ritc {list,doctor,monitor,analyze,tune,sim,run}
│   ├── core/                   # case-independent building blocks
│   │   ├── client.py           #   RIT REST API wrapper (retries, 429 handling, safe rounding)
│   │   ├── book.py             #   order book: walk/VWAP, max size within a price, microprice
│   │   ├── execution.py        #   dry-run, protective limits, slicing, IOC sweep, quotes, concurrent legs
│   │   ├── algo.py             #   block execution: passive-then-aggressive iceberg schedule
│   │   ├── feed.py             #   real-time news/tender feed (background thread, wakes the loop)
│   │   ├── tca.py              #   transaction-cost analysis vs arrival price
│   │   ├── risk.py             #   gross/net limit groups + drawdown kill switch
│   │   ├── bot.py              #   Strategy base + Runner (parallel snapshot, latency, kill switch)
│   │   └── config.py           #   .env + TOML loading
│   ├── pricing/
│   │   ├── options.py          #   Black-Scholes, Greeks, implied vol, put-call parity
│   │   ├── stats.py            #   EWMA, volatility, rolling z-score
│   │   ├── timeseries.py       #   GARCH(1,1), Kalman level, Ornstein-Uhlenbeck, ARCH test
│   │   └── news.py             #   volatility and inventory headline parsers
│   ├── strategies/             # THE FIVE BOTS: pure decision functions + a thin Strategy class
│   │   ├── liability.py
│   │   ├── derivatives.py
│   │   ├── etf.py
│   │   ├── equity.py
│   │   └── commodity.py
│   ├── tune.py                 # parallel multi-seed parameter search / A-B testing
│   ├── stress.py               # liability across 23 adverse scenarios
│   ├── postmortem/             # `ritc report`: log / journal -> reports/*.html + .xlsx
│   └── sim/server.py           # offline RIT simulator for all five case types
├── tests/                      # unit tests for every decision function + end-to-end sim runs
├── docs/                       # runbook, architecture, rules, research, liability reviews, strategies
└── .github/workflows/ci.yml    # lint + tests on Linux and Windows
```

## How the code is structured

Each strategy is split into two layers:

1. **Pure functions** such as `evaluate_tender`, `option_signal`, `plan_arb`,
   `compute_quotes` and `carry_signal`. They take numbers and order books and return
   decisions, with no network access, so they are unit-tested in `tests/test_strategies.py`.
2. **A `Strategy` class** that fetches data, calls those functions, and sends orders
   through the shared `Executor`.

The `Runner` handles everything that is the same across cases:

- waits for the case to go ACTIVE, so you can start a bot before the bell
- survives temporary API errors
- calls `wind_down()` in the last few ticks of each period
- always cancels resting orders on Ctrl-C or a crash

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Safety features

| Feature | Why |
|---|---|
| **Dry run by default** | Only `--live` sends orders. |
| **Protective limits, not market orders** | An aggressive order is a marketable limit at a worst acceptable price. It fills what is good and stops there. |
| **Immediate-or-cancel sweep** | Any unfilled remainder of an aggressive order is cancelled before the next loop. Without this, resting orders pile up and positions grow far past their targets (the simulator caught exactly this). |
| **Size from the book** | Trades are sized with `max_qty_within()`: the largest size whose blended VWAP still clears the edge. |
| **Pre-trade risk check** | `RiskManager.room()` caps every order at the gross/net room you have left (using 98% of each limit). |
| **Limit prices rounded the safe way** | Buy limits round down, sell limits round up. |
| **Clean shutdown** | Ctrl-C or any crash cancels every open order. |
| **Drawdown kill switch** | `[run] max_drawdown`: once NLV falls this far below its peak, cancel, flatten and stop adding risk. Before that, from `drawdown_soft_start`, new-risk sizes shrink gradually. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). |
| **Cost tracking** | A TCA report at shutdown shows execution cost vs arrival price for each ticker and order style. |

## Competition day

1. **Check that API trading is allowed** for each case. The bottom-bar *API Orders* icon
   must be green.
2. Copy tickers, fees, max order sizes and limits from the case brief into
   `config/<case>.toml`.
3. Run `python -m ritc doctor`. Check field names and read a few real news headlines.
   In the practice case, run `python -m ritc analyze` to choose between GARCH and EWMA.
4. Run `python -m ritc run <case> -v` (dry run) for about 30 seconds, then `--live`.

Step-by-step checklist: **[docs/RUNBOOK.md](docs/RUNBOOK.md)**.

## Development

```bash
make test        # pytest: 40+ unit tests + a full simulated case per bot
make lint        # ruff
make sim-etf     # simulator for one case
make run-etf     # dry run against whatever is on :9999
python -m ritc tune liability --grid execution.unwind_mode=block,slice --seeds 8
```

> The simulator is for checking **logic and plumbing**, not for predicting results. By
> default its other traders are random noise. `--hostile 1` (on `sim` and `tune`) adds
> manipulative competitors: pump-and-dumps, spoofing, liquidity vacuums, penny-jumpers
> and crowded tenders ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). `--queue` makes
> resting orders queue behind the displayed book (price-time priority) instead of filling
> first at the touch. Test a change in both markets. Calibrate thresholds in the official
> RIT practice cases.
