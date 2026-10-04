# ETF trading (ETF vs basket arbitrage)

**Code:** `src/ritc/strategies/etf.py` · **Config:** `config/etf.toml`

## Edge

```
NAV          = fx x sum(weight_i x price_i)
ETF rich :   ETF_bid  - NAV_ask - costs > entry_edge  ->  SELL ETF, BUY basket
ETF cheap:   NAV_bid  - ETF_ask - costs > entry_edge  ->  BUY ETF,  SELL basket
```

`plan_arb` walks **every leg's** book and binary-searches the largest ETF quantity whose
*blended* edge still clears `entry_edge`. Each leg is then sent as a protective limit at
the worst price that sizing assumed.

## Position lifecycle

1. **Open** when the plan clears the edge and the risk room for the whole package allows it.
   `room_package` counts the ETF and every component against the shared gross/net limits.
   Checking only the ETF ticker would let a 1:1+1 basket use 3× the gross we think.
   A share (`slippage_share`) of the edge above `entry_edge` widens each leg's protective
   limit, so legs still complete if the book ticks between snapshot and order.
2. **Repair** each loop with `repair_legs`. If the basket filled but the ETF did not, and
   the premium still favours that ETF trade, **complete** the arb on the ETF. Otherwise
   square the components to the ETF (`hedge_residuals`), as before.
3. **Close** (`exit_mode = "executable"`, the default) when the *reverse* arb clears
   `exit_edge` after fees and slippage. `plan_arb(directions=...)` sizes it by walking the
   books. The round trip earns at least `entry_edge + exit_edge`. A position that never
   gets there stays hedged until the close. `exit_mode = "mid"` is the old rule
   (`exit_signal`: close when the mid premium is back within `exit_edge`). That rule pays
   a second set of spreads and fees just as the gap reaches zero, and it lost money on
   the simulator. If the case has a creation/redemption converter, put its amortised cost
   in `converter_cost` and you can redeem instead.

## Variations it handles

- **Weights other than 1:1** (`components = { A = 0.5, B = 2.0 }`).
- **Cross-currency ETFs** (`fx_ticker`, `fx_mode`).
- **Tenders on the ETF:** run the `liability` bot in parallel with the ETF ticker added
  to its `tickers`.
