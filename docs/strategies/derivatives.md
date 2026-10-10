# Derivatives trading (volatility)

**Code:** `src/ritc/strategies/derivatives.py` · **Config:** `config/derivatives.toml`

## The case

An underlying (for example RTM) plus calls and puts at several strikes and expiries.
News reveals the realised volatility for the current week and a forecast range for next
week. A delta limit is enforced with per-tick penalties.

## Edge

The market's implied vol lags the news. Options are priced with Black-Scholes at **our**
forecast vol and compared with the market:

```
IV        = implied_vol(market mid)            # stdlib Newton + bisection
theo      = BS(S, K, T, r, forecast)
edge      = forecast - IV
|edge| >= vol_edge   -> target = sign(edge) x max_contracts x min(1, |edge| / full_edge)
               ...only if the EXECUTABLE price (ask for buys, bid for sells) still clears fees
hold until edge * sign(position) <= exit_edge (hysteresis, no churn)
never TRIM a position while the edge is still on our side: exit once, when it is gone
```

## Hedging

```
portfolio delta = stock + sum(position x multiplier x delta)   # every HELD option, incl. near expiry
|delta| > hedge_band x delta_limit  ->  trade the stock back to zero delta
no options left                     ->  flatten the stock
```

The delta limit is read from the news when it is published. Positions used for the hedge
are updated from **reported fills**, not orders sent. Hedging an IOC order that did not
fill puts on the delta we meant to remove, and the next loop then flips it back.

Parity trades are sized with `room_package` against every limit, because a violation can
persist for many loops. Note that the vol engine treats parity legs like any other
position and may trade them toward its own target.

## Put-call parity

For every strike/expiry with both a call and a put:

- **Conversion** (sell call, buy put, buy stock) when `C_bid - P_ask - S_ask + K·e^{-rT} > costs`.
- **Reversal** (the opposite trades) when `P_bid - C_ask + S_bid - K·e^{-rT} > costs`.

## Simulator lessons (already fixed in the code)

1. Unfilled protective limits that kept resting made positions grow to 100× their targets.
   Fixed by the core IOC sweep.
2. Without hysteresis, the bot paid the spread in and out on IV noise.
3. Leftover stock hedges after options were closed were naked directional bets.
4. **Trimming as the edge converged** was the biggest cost. The target scales with the
   edge, so every tick of IV convergence sold a few contracts and paid the spread each
   time (~7,000 contracts a run). Positions are now held until the edge is gone.
5. **News race:** prices could reprice before the feed thread had polled the headline,
   so the bot sold vol against last week's forecast just before a high-vol week. The
   Runner now polls the feed in parallel with every snapshot.
6. The delta hedge left out held options that were near expiry or had no quote, and it
   counted orders as fills. Together these made the hedge flip-flop by ±6–8k shares.

Fixes 4–6 took the 6-seed simulator mean from **-$20.3k to about +$21–27k**.

## On the day

Check `option_regex` against the real tickers, set `expiry_ticks` and `ticks_per_year`
from the brief, and confirm `parse_vol_news` reads the first headline (`doctor` prints it).

## Time series

`OnlineGarch` tracks the underlying and forecasts volatility over each option's remaining life. It is used before the first volatility headline. Blending it with the news (`garch_weight`) lost money in the simulator because the news is exact. See [ARCHITECTURE.md](../ARCHITECTURE.md).
