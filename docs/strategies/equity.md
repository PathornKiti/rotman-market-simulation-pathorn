# Equity trading (algorithmic market making)

**Code:** `src/ritc/strategies/equity.py` · **Config:** `config/equity.toml`

## Quoting: `compute_quotes`

```
fair         = microprice + imbalance_lean x half_spread x book_imbalance
reservation  = fair - skew_per_share x inventory           # long -> quote lower -> sell more
half_spread  = max(min_half_spread, vol_mult x EWMA tick vol)
bid, ask     = reservation -/+ half_spread, never crossing the opposite touch (stay maker)
sizes        = size x (1 -/+ inventory / max_inventory)    # shrink the side that adds risk
```

Past `hard_inventory`, `inventory_reduction` crosses the spread to cut the position.

## Order management

`QuoteManager` keeps one resting order per side per ticker. It only cancels and replaces
when the target price moves by `requote_tolerance` or the size changes by more than 25%.
Churning quotes loses queue priority and uses up your API rate limit.

## Tuning per ticker

Use `[strategy.per_ticker.<TICKER>]` to override any knob. Volatile names need a wider
`min_half_spread` and a stronger skew. Quiet ones can quote tighter with more size.

## End of period

`wind_down` cancels all quotes and flattens inventory, so no overnight risk.

Optional: `end_skew_boost` ramps the inventory skew to `(1 + boost)×` over the last
`end_skew_ticks`. This follows Avellaneda-Stoikov, where skew grows as time runs out.
Inventory is then shed passively before the bell instead of crossed out in `wind_down`.
It is off by default because 0 vs 3 was within noise on the simulator, which has no drift
and so little inventory risk. Try 3 in the practice case if wind-down crossing is costly.

## Time series

- `vol_model = "ewma" | "garch"`: run `python -m ritc analyze` first. GARCH only helps if volatility clusters.
- The OU `fair_shift` leans quotes toward the expected price when (and only when) mean reversion is significant at about the 1% level. See [TIME_SERIES.md](../TIME_SERIES.md).
