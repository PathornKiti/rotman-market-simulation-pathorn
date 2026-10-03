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

1. **Open** when the plan clears the edge and the risk room allows it.
2. **Repair** each loop with `hedge_residuals`: if a leg filled only partially, trade the
   components so the basket exactly offsets the ETF again.
3. **Close** with `exit_signal` when the mid premium is back within `exit_edge`. This
   captures the convergence. If the case has a creation/redemption converter, put its
   amortised cost in `converter_cost` and you can redeem instead.

## Variations it handles

- **Weights other than 1:1** (`components = { A = 0.5, B = 2.0 }`).
- **Cross-currency ETFs** (`fx_ticker`, `fx_mode`).
- **Tenders on the ETF:** run the `liability` bot in parallel with the ETF ticker added
  to its `tickers`.
