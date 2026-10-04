# Commodity trading

**Code:** `src/ritc/strategies/commodity.py` · **Config:** `config/commodity.toml`

## Engine 1: cost-of-carry arbitrage (low risk)

```
fair basis = carry_per_tick x ticks_to_expiry          # storage + financing
far rich : F_bid - S_ask - basis - costs > carry_edge  ->  SELL future, BUY spot  (cash-and-carry)
far cheap: S_bid - F_ask + basis - costs > carry_edge  ->  BUY future,  SELL spot (if spot_shortable)
exit     : |F_mid - S_mid - basis| < carry_exit
```

Carry positions are tracked separately from news positions, so a convergence exit never
closes the wrong trade. `carry_max` caps the build-up on each future.

Bookkeeping is driven by **fills**. The carry book only counts futures that actually
filled, and every loop `rehedge_spot` trades the spot until it exactly offsets the carry
futures (`spot_target`). This repairs one-legged fills. When a future expires and
cash-settles, `expire` drops it from the carry book, and the now-naked spot hedge is
unwound automatically. Before this fix, that spot leg stayed open as a directional
position. Each pair is sized with `room_package`, because the future and spot share the
gross limit. News trades are recorded only if they filled.

## Engine 2: inventory-news momentum

```
surprise = actual - expected            (build > 0, draw < 0)
move     = -impact_per_unit x surprise  (build is bearish)
|move| > news_threshold -> trade the front future, size proportional to the move
exit after news_hold_ticks, or once news_take_profit of the expected move has been captured
```

`impact_per_unit` is the knob that matters most. Calibrate it in the practice case:
record the price change in the 5–10 ticks after each report and regress it on the surprise.

## Physical assets

Commodity cases often require leasing storage, refineries or pipelines to hold or
transform the physical product. `RITClient` provides `leases()`, `lease()`, `use_lease()`
and `release_lease()`. If spot cannot be held without storage, lease it in `on_start` and
size `carry_max` to the capacity.
