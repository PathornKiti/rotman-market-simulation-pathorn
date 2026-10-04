# Risk management

The aim is to stay protected while still sizing up where the edge is real. The controls
come in three layers, from always-on to last resort.

## Layer 1: hard limits (every order)

| Control | Where | What it does |
|---|---|---|
| Per-ticker and gross/net group limits | `[risk]` in each config, `RiskManager.room()` | Every order is clipped to the room left under every limit (98% buffer). RIT fines every unit over a limit, so we never send the breaching order. |
| **Package room** | `RiskManager.room_package()` | Multi-leg trades (ETF + basket, future + spot, parity) are sized as whole packages, because all legs share the gross limit. |
| Protective limit prices | `Executor` | No naked market orders. Aggressive orders are marketable limits, cancelled at the next loop (IOC). |
| Fill-based books | `Executor.filled()` | Hedges and strategy books count what *filled*, not what was sent. |

## Layer 2: graduated drawdown throttle (all cases)

```
drawdown < soft_start x max_drawdown        -> full size
soft_start x max_drawdown .. max_drawdown   -> new-risk sizes shrink linearly to `drawdown_floor`
drawdown >= max_drawdown                    -> KILL: cancel, flatten, halt
new NLV peak                                -> full size again
```

The throttle (`[run] drawdown_soft_start = 0.5`, `drawdown_floor = 0.25`) only shrinks
**new risk**. Exits and hedges always run at full size, so a throttled bot can still get
out of what it holds.

| Case | What the throttle scales |
|---|---|
| liability | required profit per share × 1/throttle (only the best tenders while losing) |
| derivatives | `max_contracts` (existing positions are held, not dumped) |
| etf | entry `clip` |
| equity | quote size on the side that *adds* inventory |
| commodity | carry entry clip and news trade size |

It also scales every exchange limit. That matters only near a limit, because these bots
rarely get there. An earlier version scaled the limits alone and made no difference at all.

### Calibrating `max_drawdown`: a catastrophe stop, not a P&L target

Measured on 8 lock-step simulator seeds:

| Case | Worst normal drawdown | Kill level tested → effect | Default |
|---|---|---|---|
| liability | $9.4k | $8k: mean −$2.3k · $15k: no cost | **$15,000** |
| derivatives | $32k | $20k: worst seed +$0.3k → **−$14.7k** · $40k: −$0.6k mean | **$40,000** |
| equity | $2.6k | $2k: mean −$2.7k, worst seed → −$2.9k · $4k: no cost | **$4,000** |
| commodity | $100 | $100–200: no effect | **$200** |
| etf | $19–42k on runs ending **+$8k to +$51k** | — | **off** |

A stop set near the normal drawdown flattens at the bottom and locks in losses that would
have recovered. Set it at about 1.5–2× the worst drawdown you see in practice, rescaled
to the real case's P&L.

**Why ETF is off:** a hedged convergence trade's mark-to-market is at its worst exactly
when the mispricing is widest, which is the best time to hold or add. ETF risk is
controlled by package room, leg repair and `[risk]` limits instead.

## Layer 3: case-specific guards

| Case | Guard | Config | Effect |
|---|---|---|---|
| liability | GARCH risk premium on unwind time; min ticks left; risk room per tender | `risk_aversion`, `min_ticks_to_unwind` | Bigger edge needed when the market is volatile or time is short |
| liability | Queue-aware pricing (opt-in) | `price_queue` | Lower variance, lower mean on the simulator |
| derivatives | Delta limit with band re-hedge, from fills, all held options | `hedge_band`, news | Never fined, no hedge flip-flop |
| derivatives | **Vega budget** | `max_vega` | Caps $ per vol point; biggest edges get the budget first |
| etf | Leg repair / completion; slippage budget from surplus edge | `hedge_tolerance`, `slippage_share` | No unhedged legs left |
| equity | Hard inventory limit; size taper | `hard_inventory`, `max_inventory` | Inventory never runs away |
| equity | **Jump guard** | `jump_sigmas`, `jump_pause_ticks` | Pulls quotes after an outsized move |
| commodity | Spot re-hedge to the carry book; expiry handling | — | No naked spot leg |
| commodity | **News stop-loss** | `news_stop` | Exit when the move goes the wrong way |

## Results

See [PERFORMANCE.md §7](PERFORMANCE.md) for the before/after numbers with all protections
on.
