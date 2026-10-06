# Performance: speed, block execution, cost tracking, kill switch, tuning

## 1. Real-time processing (speed)

| Technique | Where | Effect |
|---|---|---|
| **Event feed**: news and tenders polled on a background thread every `feed_poll` s (default 100 ms). The loop *waits on an event* instead of sleeping, so it wakes the moment something arrives. | `core/feed.py`, `Runner.run` | Reaction to a headline or tender is ~100 ms + one loop instead of up to a full `interval` + one loop. Used by `liability` (tenders), `derivatives` and `commodity` (news). |
| **Parallel snapshot**: case, securities, NLV and every order book the strategy declares in `book_tickers` are fetched concurrently. | `RITClient.parallel`, `Runner.snapshot` | One round trip per loop instead of 3 + one per ticker. |
| **Concurrent legs**: multi-leg trades (ETF vs basket, futures vs spot) are sent in parallel. | `Executor.limit_many` | Shorter gap between legs means less leg risk. |
| **Thread-safe client**: one HTTP session per thread, global request throttle. | `RITClient.session` | Safe concurrency; `RIT_MIN_INTERVAL` still caps the request rate. |
| **Latency telemetry**: p50/p95/max loop time logged every 200 loops and at shutdown, plus a warning for any loop slower than 500 ms. | `Runner`, `LatencyStats` | You know whether you are trading on stale data. In the simulator: p50 ≈ 15 ms, p95 ≈ 24 ms. |

## 2. Block trades: passive-then-aggressive execution

`core/algo.py` works a large position to a target by a deadline:

```
schedule   linear or front-loaded path from start to target (schedule_position)
on/ahead   rest an ICEBERG child at the touch (one tick inside a wide spread)  -> earn the spread
behind     cross for the shortfall, sized so the slice VWAP stays within max_slippage of the touch
urgent     last `urgent_ticks`: cross for everything left (protective limit only)
limit      never trade through `limit_price`
```

Progress is measured from the **position**, so partial fills, resting fills and manual
trades are all handled. The liability bot uses it to unwind tenders
(`[execution]` in `config/liability.toml`). A new tender on the same ticker restarts the
schedule from the new position.

**Result** (`python -m ritc tune liability --grid execution.unwind_mode=block,slice --seeds 8`):

| unwind_mode | mean NLV | worst seed | seeds won |
|---|---|---|---|
| **block** | **$49,711** | **$22,614** | **8 / 8** |
| slice (always cross) | $24,109 | $8,219 | 0 / 8 |

TCA from one run shows the source of the gain. Passive children **earned** 1.5–1.9¢/share
against arrival mid, while aggressive catch-up slices **cost** 1.8–3.0¢/share.

> The simulator's passive fill model is simple: a fixed fill probability at the touch
> and no queue position. Real passive fill rates will be lower, so expect a smaller
> gain than shown here. Check it with the TCA report in the practice case. If passive
> fill rates are very low there, raise `front_load` or shorten `unwind_horizon_ticks`.

Schedule tuning (6 seeds): `horizon=60, front_load=0` scored highest ($55.8k), ahead of
the default `30 / 0.3` ($51.7k), but the gap is within noise (stdev about $14k).
Longer schedules also depend more on the optimistic passive fills, so the default stays
at 30 / 0.3.

## 3. Transaction-cost analysis (TCA)

Every fill is compared with the arrival mid at decision time (`core/tca.py`). Immediate
fills come from the order response. Later fills (resting quotes, IOC remainders) are
reconciled from `/orders` every 20 loops and at shutdown. The report prints when a live
run ends:

```
TCA (shortfall vs arrival mid; + = cost, - = earned)
ticker    style           filled   fill%    $/unit     total $
CRZY      aggressive       57937     99%    0.0179    1,038.88
CRZY      passive         140913     33%   -0.0189   -2,660.11
```

Use it to see which bot or ticker leaks money in execution and whether passive posting
is really earning the spread.

## 4. Drawdown kill switch

`[run] max_drawdown` (in $; 0 = off). The Runner tracks peak NLV every loop. Once NLV
drops `max_drawdown` below the peak, it trips permanently for that run:

- all risk room goes to zero
- every order is cancelled
- every position is flattened with protective limits

Set it from the case's typical P&L swing, for example 2–3× the worst drawdown you see in
practice. Too tight and normal noise stops you trading.

## 5. Tuning harness

**Lock-step simulator (default, `--speed 0`).** The market advances one tick every 4 bot
loops instead of on a wall clock, so a run depends only on the seed and the settings.
Before this, the same code on the same seed varied by up to $10k between runs on equity:
thread timing decided which quotes were resting when a price jumped. `--speed N` still
runs in real time.

**Common random numbers for passive fills.** The passive flow hitting each side of each
book on each tick is drawn from a generator keyed on `(seed, tick, ticker, side)`. Every
setting therefore meets the *same* incoming flow, and orders at the touch share it best
price first, then by time. Before this change, fills came from one sequential stream that
was drawn once per resting order at the touch. Any setting that rested one more order
reshuffled every later fill, so two settings had unrelated fill luck. Measured on equity
over 16 seeds:

| Paired comparison | Paired SE, sequential fills | Paired SE, common random numbers |
|---|---|---|
| `imbalance_lean` 0.25 vs 0 | $809 (diff +$1,731, t 2.1) | **$234** (diff −$62, t −0.3) |
| `jump_sigmas` 4 vs 0 | $821 | **$137** |
| `requote_tolerance` 0.01 vs 0.02 | $394 | $412 (a real effect: wider tolerance leaves quotes off the touch) |

A 3.5–6× smaller standard error needs 12–36× fewer seeds to detect the same effect. The
first row shows the danger: under the old fills, the imbalance lean looked like a
significant +$1.7k, and it was entirely fill luck.

```bash
python -m ritc tune equity --grid strategy.min_half_spread=0.02,0.03     # tune: seeds 1-16
python -m ritc tune equity --grid strategy.min_half_spread=0.03 --first-seed 101   # confirm on fresh seeds
```

How to read the table:

- The config baseline always runs. Every other row shows its **paired** difference to the
  baseline on the same seeds (`vs base`), that difference's standard error, `t`, and the
  number of seeds it beat the baseline on.
- **Adopt only if t ≥ 2 and the worst seed is no worse.** |t| < 2 means no evidence either
  way; keep the simpler setting.
- **Correct for how many settings you tried.** With m settings in the grid, some reach
  t ≥ 2 by luck alone. `p adj` is the paired two-sided p-value × m (Bonferroni), and the
  footer prints the |t| needed for family-wise 5% (e.g. 5 settings on 8 seeds: 3.50
  instead of 2.36). On a grid, shortlist only rows with `p adj` < 0.05.
- **Confirm on fresh seeds.** The best row of a big grid is partly luck (winner's curse).
  Re-run just the winner with `--first-seed 101` and adopt only if it still has t ≥ 2.
  The confirmation run tests one setting, so the plain t ≥ 2 applies there.
- The default is now 16 seeds (was 5). Below about 12, the standard error on equity is
  too large to separate the effects worth having.
- `wins` counts seeds where a setting was strictly best. Ties count for nobody.

What the simulator **cannot** tell you (confirm these in the RIT practice case):

- **Book-shape signals.** Book sizes are random, so microprice, imbalance, VAMP and
  order-flow signals carry no information here.
- **Spread width.** A quote at or inside the touch fills at the same rate, so only the
  price received changes. There is no queue against other traders.
- **Competitors and shocks.** There are no rival algorithms and no inventory shocks, so
  anything about crowding, liquidity droughts or forced liquidation is untested.

## 6. Per-case review: what changed and what it measured

Same 6 simulator seeds (`python -m ritc tune <case> --seeds 6`), mean final NLV, before → after.
Simulator P&L is noise-driven: read these numbers as direction, not as a forecast.

| Case | Before | After | Main change |
|---|---|---|---|
| derivatives | **-$20.3k** (worst -$45.8k) | **+$21.5k to +$27.2k** (worst -$0.7k to -$6.2k) | no trimming while the edge holds; news polled with every snapshot; hedge counts fills and every held option |
| etf | **-$0.5k** (worst -$5.8k) | **+$16.0k to +$17.7k** (worst +$3.5k to +$5.6k) | executable exit instead of mid-premium exit; leg completion; slippage budget; package risk room |
| commodity | +$291 (worst -$244, stdev $321) | **+$373** (worst **+$309**, stdev $64) | fill-tracked carry book, spot re-hedge, expiry handling, package risk room |
| liability | +$47.3k | +$46.1k (noise) | rejected competitive accepts no longer booked |
| equity | +$1.3k | -$3.0k to +$1.1k across repeat runs (noise) | none (the lock-step simulator in §7 resolved this noise) |

Cross-cutting (`core/`):

- **News race.** `Runner.snapshot` polls the event feed *in parallel with* the prices.
  The bot never sees a repriced market without the headline that moved it. This
  matters for derivatives (vol news), commodity (inventory reports) and liability
  (tenders).
- **`RiskManager.room_package(legs, positions)`** gives the max number of whole
  multi-leg packages that fit every limit. All legs share the gross limit, so
  checking one ticker at a time over-sizes ETF and carry trades.
- **`Executor.filled(responses, qty)`** counts what actually filled. Strategies that
  keep their own books (carry, news, hedges) must count fills, not orders.

## 7. Risk-managed and tuned (lock-step simulator)

8 seeds, lock-step (`python -m ritc tune <case> --seeds 8`). "Before" is the first lock-step
measurement after §6's fixes. "After" adds the jump guard, the graduated throttle, the
kill-switch defaults, the case guards and the tuned sizes. Every case runs with its
protections ON.

| Case | Before: mean / worst seed | After: mean / worst seed | What moved it |
|---|---|---|---|
| liability | $31.6k / $16.9k | **$34.1k / $16.9k** | `min_profit_per_share` 0.03 → 0.01 (the throttle raises it again in a drawdown); kill at $15k |
| derivatives | $17.4k / −$1.1k | **$17.3k / +$0.3k** | throttle + $40k kill; size kept at 60 contracts (100 = more mean, worse tail) |
| etf | $31.1k / $7.8k | **$32.8k / $10.4k** | `entry_edge` 0.08 → 0.06; package room, leg repair |
| equity | $7.6k / $3.2k | **$8.1k / $5.9k** | jump guard (4σ, 2-tick pause); kill at $4k |
| commodity | $367 / $317 | **$409 / $349** | `news_max_size` 40 → 60 with the new `news_stop` |

What did **not** help. Removed from the code, or kept at the old setting:
- a drawdown stop near the normal drawdown (derivatives $20k: worst seed −$14.7k)
- a binding vega cap
- equity end-of-period skew
- liability `price_queue`
- larger ETF `clip` or commodity `carry_clip`

Details are in [RISK.md](RISK.md).
