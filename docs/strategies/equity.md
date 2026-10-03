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
