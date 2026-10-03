# Liability trading (tender offers)

**Code:** `src/ritc/strategies/liability.py` · **Config:** `config/liability.toml`

## The case

Institutional clients offer you blocks (for example, "buy 30,000 CRZY at $24.85").
A fixed-price tender is take-it-or-leave-it. A competitive tender asks for your bid.
Once you accept, you hold the block and must unwind it before the close. Your P&L is
the gap between the tender price and your unwind VWAP, minus commissions.

## Decision: `evaluate_tender`

```
offset          = part of the block that cancels an existing opposite position (no unwind needed)
unwind VWAP     = walk(book with every level x refill_factor, remaining quantity)
drift cost      = max(0, adverse recent drift) x expected unwind ticks
profit / share  = sign x (unwind VWAP - tender price) - fee - drift cost
ACCEPT  iff  profit/share >= min_profit_per_share
         and quantity <= risk room
         and ticks_left >= min_ticks_to_unwind
```

For **competitive** tenders, the bid is the price that leaves exactly
`competitive_margin` per share. That is the most competitive price that is still
profitable for us.

## Execution: `unwind_slice`

Each loop: take the largest quantity whose VWAP stays within `max_slippage` of the touch,
capped at `participation` × visible depth and the max order size. In the last
`urgent_ticks` the slippage allowance and participation scale up to 4×. In the final
`wind_down_ticks` whatever is left is dumped. An open position at the close is pure risk.

## Tuning

| Knob | Raise it if… | Lower it if… |
|---|---|---|
| `refill_factor` | the book refills fast after you hit it | your unwinds keep losing money |
| `min_profit_per_share` | you take tenders that end up flat or negative | you decline tenders that other teams profit from |
| `max_slippage` | you are not flat by the close | your unwind VWAP is far from the touch |

## Ideas to add

- Learn `refill_factor` live: compare the visible depth before and after your unwinds.
- When several tenders are open, rank them by profit per unit of limit used.

## Time series

The tender price includes a risk premium of `risk_aversion × GARCH price vol × √(unwind ticks)`. A block is worth less when the market has just turned volatile. See [TIME_SERIES.md](../TIME_SERIES.md).

## Block execution

Unwinds use the passive-then-aggressive block algorithm (`[execution]` section). It
rests an iceberg at the touch while on schedule and crosses only to catch up. In the
simulator it beat the always-aggressive slice unwind on 8 of 8 seeds (mean $49.7k vs
$24.1k). Tenders arrive through the real-time feed. See [PERFORMANCE.md](../PERFORMANCE.md).
