# Architecture

```
            config/<case>.toml           .env (API key, host, dry-run)
                   │                            │
                   ▼                            ▼
 python -m ritc run <case> ──► cli.build() ──► RITClient ──HTTP──► RIT Client :9999/v1
                                   │                                (or ritc.sim on :9999)
                                   ├─► Executor  (dry-run, protective limits, slicing, IOC sweep)
                                   ├─► RiskManager (gross/net limit groups)
                                   ├─► Journal (logs/*.jsonl, the run's data for the report)
                                   └─► Strategy  (one of five) ◄── Runner loop
```

## The loop (`core/bot.py`)

```
every `interval` seconds (or as soon as the event feed sees a tender / headline):
    executor.sweep()               # cancel unfilled remainders of last loop's aggressive orders
    snap = case + securities + trader + every book the strategy needs, fetched in parallel
    if not ACTIVE: wait            # safe to start before the bell
    journal: one `tick` record per new tick (NLV, positions, bid / ask / last, slowest loop)
    last N ticks: strategy.wind_down(snap)   else: strategy.step(snap)
on Ctrl-C / crash / STOPPED: cancel_all(), TCA report, journal `end`
```

| Layer | Knows about | Does not know about |
|---|---|---|
| `core.client` | HTTP, RIT endpoints | strategies |
| `core.book` | order-book maths (walking the book) | the network |
| `core.execution`, `core.algo` | sending orders safely; block (passive-then-aggressive) execution | why |
| `core.risk` | limits and positions | prices |
| `core.journal` | writing the run journal | anything else |
| `pricing.*` | maths and parsing | the API |
| `strategies.*` | decision logic (pure functions) + the Strategy class wiring it | the network |
| `sim.server` | a simulated RIT server for every case | the bots |
| `postmortem.*` | turning logs + journals into reports | trading |

Speed: tenders and news are polled on a background thread every 100 ms and wake the loop; the snapshot is one
parallel round trip; multi-leg orders go out concurrently; loop latency is logged (p50/p95/max) and any loop
over 500 ms is a `slow_loop`. Adding a strategy: a module with pure decision functions and a `Strategy`
subclass, a `REGISTRY` entry, `config/<case>.toml`, tests, optionally `_setup_<case>` / `_tick_<case>` in the
simulator.

RIT API notes: `/securities/book` returns singular keys (`bid`, `ask`); there is no IOC order type
(`Executor.sweep()` emulates it); a 429 says how long to wait (honoured, capped at 2 s; `RIT_MIN_INTERVAL`);
tenders: `POST /tenders/{id}` accepts (`price=` for auctions), `DELETE` declines; leases: `GET/POST /leases`.

## Execution (`core/algo.py`)

Blocks are worked to a target by a deadline: rest an iceberg child at the touch (one tick inside a wide spread)
while on schedule, cross for the shortfall when behind (slice VWAP within `max_slippage` of the touch), cross
everything in the last `urgent_ticks`. The schedule is Almgren-Chriss (`sinh` path from GARCH sigma and
book-implied impact). Progress is measured from the position, so partial and resting fills are handled.
Liability holds to the bell: inside `close_hold_ticks` and while the 1-sd $ risk is within `hold_risk_budget`
it only rests. TCA (`core/tca.py`) compares every fill with the arrival mid and prints at shutdown.

## Risk controls

1. **Hard limits on every order**: per-ticker and gross/net group limits (`RiskManager.room()`, 98% buffer),
   whole-package room for multi-leg trades, protective limit prices (no naked market orders), books counted
   from fills, not orders.
2. **Graduated drawdown throttle**: from `drawdown_soft_start x max_drawdown` new-risk sizes shrink to
   `drawdown_floor`; at `max_drawdown` the kill switch cancels, flattens and halts. Exits always run at full
   size. Calibrated as a catastrophe stop only: a stop near the normal drawdown sells reverting drawdowns
   (derivatives at $40k turned a +$40.3k heat into -$14.0k; equity's $4k stop was hunted by pumps and blocks,
   off = +$951, t 3.0). Off for liability, ETF and equity; derivatives $80k, commodity $200.
3. **Case guards**: liability GARCH risk premium, minimum ticks to unwind, risk room per tender, crowd
   learning, front-running freeze, booking tracking, late answers; derivatives delta band from fills;
   ETF leg repair and slippage budget; equity hard inventory, size taper, jump guard; commodity spot re-hedge
   and news stop-loss.

Tested and removed (numbers in [RESEARCH.md](RESEARCH.md)): binding vega cap, scenario-CVaR position limit,
equity end-of-period skew, price bands, spoof-capped depth, queue fighting, toxicity-adaptive spread.

## Time-series models (`pricing/timeseries.py`)

- **GARCH(1,1)** by maximum likelihood with variance targeting (only alpha, beta fitted); `OnlineGarch` uses
  EWMA (lambda 0.94) until 60 returns, then refits every 50 ticks; `term_vol(h)` over a horizon. Liability's
  risk premium and Almgren-Chriss speed use it. Perfect vol knowledge adds $0 to liability (oracle test), so no
  better vol model is worth building.
- **Kalman level** (denoised price) and **OU mean reversion** (AR(1); used only when the Dickey-Fuller-style
  t < -3.5: 1% false positives on random walks). Equity's fair-value shift.
- `python -m ritc analyze` on real data: `clusters = YES` -> equity `vol_model = "garch"`; `mean-rev = YES`
  -> the equity bot leans toward the OU forecast automatically.

## The simulator (`sim/server.py`)

One `Market` per case, serving the same endpoints as the RIT client. Rules from the official case packages
where they exist ([OFFICIAL_RULES.md](OFFICIAL_RULES.md)); the liability market follows the 2027 brief.
`RITC_STRESS="k=v,..."` knobs push its unknowns (vol, depth, tender edge / size / gap, booking delay, strict
front-running, resting-fill flow, maker fee, spread, close slip, bell dump, crowd at expiry, vol regimes,
`anchor`); `ritc stress` runs 23 named scenarios built from them.

**Hostile mode** (`--hostile 1`; 0 = exactly the benign market): pump-and-dump, spoofing (fake size behind
the touch that never fills), liquidity vacuums, penny-jumpers in front of our resting quotes, crowded tenders
(liability: three other desks unwind the same block; half the move stays) and competing ETF arbitrageurs. All
from their own seeded generators, so settings compared on a seed meet the same manipulation.
`RITC_HOSTILE_THREATS` switches threats one at a time. Use `anchor=1` (tenders priced off the visible mid):
pricing off the true mid hands the bot a fake "displacement edge". A change must not cost money in the benign
market to count.

## Fair testing (`ritc tune`, `ritc stress`)

- **Lock-step**: the market advances one tick every 4 bot loops, so a run depends only on seed and settings
  (wall-clock runs varied by $10k on the same seed). The tuner talks to the market in-process (antivirus can
  slow loopback HTTP 60x).
- **Common random numbers**: passive flow is drawn per (seed, tick, ticker, side), so settings share fill luck
  (paired SE 3.5-6x smaller).
- **The rule**: adopt only with paired t >= 2 on seeds 1-16 (or 1-32), `p adj` < 0.05 on a grid (Bonferroni),
  a confirmation on fresh seeds (`--first-seed 101`), no worse worst seed / CVaR, and no loss in the realistic
  hostile market. Everything that failed was deleted and recorded in RESEARCH.md.
- What the simulator cannot tell you: book-shape signals, queue priority (unless `--queue`), real competitors.

## Run journal and reports (`core/journal.py`, `postmortem/`)

`ritc run` writes `logs/<strategy>-<time>.jsonl` next to the text log: `start` (case, trader, config),
`tick` (NLV, positions, bid / ask / last, slowest loop), `order`, `fill`, `tender_seen` (with the book then),
`tender_decision` (estimate, reason), `tender_answer` (server reply), `error`, `slow_loop`, `end` (latency,
TCA). Flushed line by line; the tuner and replays write none.

`python -m ritc report` (`postmortem/`): `logparse` reads every log format; `simmatch` finds the simulator seed
behind a local run from its tender stream; `replay` re-runs the current bot on that market fully instrumented
(exact P&L decomposition, FIFO attribution, fines under both front-running readings, per-tender
counterfactuals); `findings` runs the gap checks; `live` turns a journal into the P&L path and the struggle
distribution; `html` / `xlsx` / `stress_report` write the reports. What they say: [POSTMORTEM.md](POSTMORTEM.md).
