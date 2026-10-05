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
loops instead of on a wall clock. The market and fill randomness use separate seeded
generators, so a run depends only on the seed and the settings. Before this, the same code
on the same seed varied by up to $10k between runs on equity: thread timing decided which
quotes were resting when a price jumped, and every resting order shifted the random price
path. Comparisons there were noise. `--speed N` still runs in real time.

```bash
python -m ritc tune liability --grid execution.unwind_horizon_ticks=15,30,60 \
                              --grid execution.front_load=0,0.3 --seeds 6
```

- Every setting runs the **same seeds** (a paired comparison) in separate processes, in
  parallel.
- Results are ranked by mean NLV, with the worst seed, stdev and seeds won.
- Prefer settings that win on most seeds and have a good worst case over the single best
  mean.
- Use it to reject bad ideas and compare variants, then confirm in the RIT practice case.
  The simulator's other traders are noise, not teams.

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
