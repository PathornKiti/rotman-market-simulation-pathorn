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
