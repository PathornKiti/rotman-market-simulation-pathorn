# Trend-Hold Tender Strategy (`rsi_bot`): Theory and Methodology

*Companion: `docs/RSI_TRADING_FLOW.md` (step-by-step trading flow).*

## 1. Problem setting

In the Liquidity Risk case, institutions offer the desk private **fixed-price tenders**: blocks of about 50,000 shares of one stock (CRZY, TAME or CROC) at a stated price. Accepting a tender creates a large position that must be closed in the open market before the end of the heat.

The desk's profit on a tender has two parts:

- **The tender premium:** how far the tender price is from the market price when the tender is accepted.
- **The liquidation result:** the prices at which the position is later closed, net of spread, fees and market impact.

The standard approach accepts tenders with a positive expected premium after liquidation costs, then liquidates at once to limit price risk. This strategy keeps the acceptance logic but treats **when to liquidate** as a decision in its own right, based on the price trend that is in progress.

### Case constraints that shape the strategy

| constraint | consequence |
|---|---|
| Position limits: gross 250,000 shares, net 100,000 shares | A position that is held takes up limit room that later tenders would need |
| Open positions are closed at the last traded price at the end of the heat, with no penalty | Holding to the bell is a valid, costless way to exit |
| A position from an accepted tender is not speculation | Holding a tender block is never fined |
| Trading beyond the tender position is speculation, fined at $0.20/share up to 5,000 shares and $0.60/share above 5,000 | The strategy never adds to or reverses a position on its own; it only decides when to close tender blocks |
| Fees of $0.02/share on market orders, with finite book depth | Liquidating quickly has a cost that grows with the selling rate |

## 2. Theory

### 2.1 Price dynamics are trending, not mean-reverting
On the recorded real-market price paths, the stock prices show **positive short-horizon autocorrelation**:

- The correlation between consecutive 10-tick returns is about +0.42.
- After the strongest 20% of 20-tick moves, the price continued in the same direction by about $0.24/share over the next 50 ticks (t ≈ 10.5).
- High RSI(10) readings (≥ 80) were followed by further moves *in the same direction* (about +$0.22/share over 50 ticks).

So oscillator extremes do not signal reversals in this market; they signal persistence. Every rule below is built on this.

### 2.2 Implication for liquidation timing
Suppose a long block of size *Q* is acquired at price *P* while the mid price *m* is rising. Liquidating at once realises roughly

  *Q* · (*m*₀ − *P*) − liquidation cost.

Holding while the trend continues adds *Q* · Δ*m*, where Δ*m* is the further move in our favour. With positive autocorrelation, the expected Δ*m* given a recent favourable move is positive. Once the recent move turns adverse, the expected Δ*m* is no longer positive.

The optimal policy is therefore to **hold while the recent trend is favourable and liquidate when it turns**. This is a momentum stopping rule applied to an existing inventory.

### 2.3 Recovery is not symmetric
The reverse argument does **not** hold for blocks that are already losing. Once a block was $0.10-0.50/share against us, keeping it lost a further ~$0.20/share on average by +50 ticks and at the bell. The price came back to the entry mid in 39-63% of cases, but in only 28-42% of cases before falling another $0.30.

So the strategy holds winners and does not hold losers waiting for a recovery.

### 2.4 Acceptance follows from the holding policy
If blocks are held through favourable trends, a tender is worth more than its immediate-liquidation value. A tender priced better than the best quote on the side we would trade (the **touch**) has a positive premium against the executable price before any trend gain. Such tenders are therefore worth accepting even when an immediate-liquidation valuation (which charges impact and crowding costs) would reject them.

### 2.5 Crowding
Rival desks may receive similar tenders and liquidate the same stock at the same time. That shortens trends and pushes prices against held inventory.

The bot runs a Bayesian **crowd estimate**: it measures how prices move after tenders it did not take, and raises an alarm when the estimated impact is significantly positive (posterior mean > 2 sd). When crowding is detected, the evidence needed to *start* a hold is raised.

## 3. Methodology: decision rules

Each rule is applied on top of the bot's existing tender valuation and execution engine.

### Rule A: Touch acceptance
For a fixed tender that the valuation model declines **on price only**:

- BUY tender: accept if `best_bid − tender_price > θ_touch`.
- SELL tender: accept if `tender_price − best_ask > θ_touch`.

Here `θ_touch = 0`. Declines caused by limit room or time to the bell are never overridden, and auctions are excluded.

### Rule B: Momentum hold
For each new inventory block (a tender booked, or a position that grows or changes sign), let *s* = +1 for a long block and −1 for a short block, and let *m*ₜ be the mid price at tick *t*. Define the signal

  *r*ₜ = *s* · (*m*ₜ − *m*ₜ₋ₙ), with *n* = 10 ticks.

1. **Start (at the block's first decision tick):** hold if *r*ₜ ≥ θ_start. Otherwise liquidate at once.
2. **Continue:** re-evaluate every *k* = 10 ticks, and keep holding while *r*ₜ ≥ 0.
3. **Stop:** at the first evaluation with *r*ₜ < 0, the hold ends for good for that block. The block is then liquidated by the standard execution engine (front-loaded slice, an Almgren-Chriss style schedule, and acceleration when the drift turns adverse).
4. **Terminal:** a block still held at the end of the heat is closed at the last traded price, at no cost.

### Rule C: Crowding-conditional start threshold
θ_start = $0.00/share when no crowding is detected, and $0.05/share while the crowd alarm is on. Only the start condition changes; continuation always uses *r*ₜ ≥ 0.

### Parameters
| parameter | key | value |
|---|---|---|
| θ_touch | `strategy.accept_touch_edge` | 0.0 |
| momentum window *n* | `execution.trend_hold_ticks` | 10 |
| evaluation interval *k* | `execution.trend_hold_check_ticks` | 10 |
| signal | `execution.trend_hold_signal` | `"momentum"` (`"ml"` = model forecast variant) |
| θ_start (no crowding) | `execution.trend_hold_min_start` | 0.00 |
| θ_start (crowding) | `execution.trend_hold_min_start_crowded` | 0.05 |

All of these are off in the base configuration and turned on only by the `rsi_bot` profile. Implementation: `touch_override()` and `trend_hold()` in `src/ritc/strategies/liquidity.py`.

## 4. Methodology: validation

### 4.1 Counterfactual replay on recorded paths
Live results depend on which tenders a heat happens to offer. Comparing profiles that ran in different heats therefore mixes strategy and luck. Instead, every candidate policy is replayed on **the same recorded price path and tender sequence** of each real heat:

- Exits are gradual, at 2,500 shares/tick at mid − $0.03, plus the $0.02/share fee.
- Positions still open at the bell close at the last price.
- Gross and net limits are enforced.

The baseline is the standard bot's own acceptances, liquidated at once.

### 4.2 Statistical criteria
- **Paired comparison:** the per-heat difference (candidate − baseline) on the same heat, summarised by its mean and a paired t-statistic. Adoption requires t ≥ 2.
- **Holdout:** the result must also hold on heats recorded after the rule was designed, and on both halves of the sample split by date.
- **Model-based variants** (ML forecast as the signal): evaluated only on heats after the model's training cutoff.
- **Robustness by market conditions:** heats are split into terciles by other participants' traded volume (market volume minus the desk's own fills), and by crowd-alarm status.
- **Distribution, not only the mean:** median, 10th percentile and the share of heats above a target are reported, because the P&L distribution is skewed.

### 4.3 Design choices that came out of this process
| alternative | finding | decision |
|---|---|---|
| RSI(10) reversion exit (70/30) | worse than the momentum hold, as expected from §2.1 | rejected |
| RSI 60/40 trend exit | +$18k/heat vs +$26k for the 10-tick momentum rule | momentum chosen |
| Shorter window or more frequent checks (5 ticks) | −$1.7k to −$5.1k/heat: exits too early on large trends | n = k = 10 kept |
| ML 10-tick forecast as the signal | statistically indistinguishable from momentum (t 1.3) | momentum kept for simplicity |
| Trailing stops | no improvement | rejected |
| Hold every block to the bell | higher mean, unacceptable tail (worst heat −$220k) | rejected |
| End holds to free limit room for new tenders | +$2.0k/heat, t 1.7 | below threshold, not adopted |
| Hold a fixed fraction (50%) of each block | mean −$2.3k/heat; 10th percentile roughly doubled | an alternative risk setting, not adopted |
| Remove touch acceptance | −$7.7k/heat (t −4.3) | touch acceptance kept |

### 4.4 Summary of the validated effect
Over 70 heats outside the forecast model's training period, the strategy beat the baseline by **+$20.4k/heat (t 3.9)**. On the most recent 29 heats, used as a holdout, it beat it by **+$19.7k/heat (t 2.3)**.

The advantage gets smaller as participant volume rises:

| market | advantage vs baseline |
|---|---|
| quietest tercile | +$39.1k/heat |
| busiest tercile | +$21.5k/heat |
| sessions with about six live participants | +$17.7k/heat (t 2.5) |

This matches the theory in §2.5: crowding shortens trends.

## 5. Known limitations
- **Crowding beyond the observed range:** the effect has not been measured for participant volume above about 7.5M shares/heat.
- **Crowd alarm sensitivity:** the alarm learns from tenders the desk declines. Because touch acceptance declines fewer tenders, the alarm gets fewer observations and fires less often under this strategy.
- **Limit occupancy:** held blocks use up net-limit room (up to about 92k of 100k), so the desk occasionally has to decline a profitable tender.
- **Skewed P&L:** a small number of long trends account for a large share of profit, so single-heat results vary widely.
