# Summary of changes: what was added, what was removed

Every change below was measured on the simulator before being kept. The rule was
simple: **a change stays only if it improved results.** Anything that didn't was
removed from the code, and its measurements are kept in [RESEARCH.md](RESEARCH.md).

Numbers are mean final NLV across 8 seeds, with the worst seed in brackets, on the
reproducible lock-step simulator unless noted. Simulator P&L is noise-driven, so read
them as direction, not as a forecast.

## 2026-10-09: why the losing seeds lose; a queue model; a faster tuner

**Losing-seed diagnosis** (per-seed P&L attribution on the current code):

| Case | Seeds | What loses the money |
|---|---|---|
| ETF | 113 (−$1.5k), 101, 116 (~$7.5k) | **FX on the USD balance.** RITC closes at the CAD basket value, so in CAD the ETF leg is a CAD asset and the USD paid for it is a naked FX position. FX P&L over 32 seeds: mean −$1.9k (t −1.5, noise), **sd $7.3k/heat**. Seed 113: −$21.0k of FX. Without FX the worst of 32 seeds is +$17.0k, and most of the "holdout is worse" gap goes away (holdout FX ≈ −$4.6k/heat, seeds 1–16 ≈ +$0.5k). |
| Derivatives | 112 (+$15k, the worst; no losing seed any more) | Not a bug. Weekly vol barely changes (0.20–0.30), so the edge is small, but the bot still flips its whole option book when the forecast flips: spread + fees $48k vs $63k gross. |
| Equity | 104, 105, 115 | Inventory held through a trend (seed 105: all three names +$1.0–1.3) and assigned blocks. Quotes are run over when the mid jumps through them, so the inventory builds against the move. Spread capture and rebates are positive. |

| Case | Change | Benign 1–16 / 101–116 | Hostile 1–16 / 101–116 | |
|---|---|---|---|---|
| ETF | **`fx_hedge_band`** 50k: market-order the USD balance back to flat. Effect per seed = −(FX P&L) − **$1.27k** (sd $0.35k): the half-spread on the arb's USD turnover | −$1.8k (t −1.5) / +$3.2k (t 1.5); 32 seeds mean $30.6k → $31.2k, **sd $12.3k → $9.9k, worst −$1.5k → +$16.4k**, CVaR(8) $15.1k → $20.0k | −$2.6k (t −1.7) / +$0.1k (t 0.1); worst $23.2k → $27.5k / $6.5k → $8.5k | **code kept, off**: a risk trade, not a mean gain (fails t ≥ 2). Owner's call |
| ETF | hedge with an IOC limit at the USD touch instead of market orders | identical to market orders on all 32 seeds (the hedges fit inside the touch) | — | removed |

**Simulator: `--queue`** (sim and tune; off = the old fill model, unchanged). Price-time priority
against the displayed book: a resting order joins behind the real shares shown at its price, passive
flow eats that queue first, 20%/tick of it cancels (`QUEUE_CANCEL`, an assumption), and improving
the touch puts us first. Effect on the current bots (32 seeds, benign / hostile): equity
$4.7k → $1.8k / −$0.5k → −$1.3k; ETF unchanged ($35.2k → $35.1k on 1–16; its passive quotes sit off the
touch). No equity setting beats the config under the queue model, in either market
(requote tolerance, `min_half_spread`, `size`, `skew_per_share`, `jump_sigmas`; RESEARCH.md).

**Tuner: in-process transport.** Lock-step runs now call the simulator in-process (`InProcessAdapter`,
the same `route()` the HTTP server uses) instead of over loopback HTTP. Results are identical
(checked seed by seed), and a run takes ~2.4 s instead of ~12 s. On this machine the antivirus
was intercepting loopback HTTP and stretched a run to 12 minutes.

## 2026-10-08 (later): each bot adapts to its own case's events

| Case | Event | Change | Seeds 1–16 (benign / hostile) | Holdout 101–116 (benign / hostile) | |
|---|---|---|---|---|---|
| Derivatives | weekly vol headlines | **`vol_term`**: each option's forecast is the variance-weighted mix over ITS remaining ticks: the rest of this week at this week's realised vol, the rest at next week's announced range mid. It used to blend the range into one number for every option. | **+$26.4k, t 6.3** / **+$26.0k, t 6.2**, 15/16; worst $32.8k → $43.3k | **+$30.6k, t 6.1** / **+$29.6k, t 6.1**, 16/16; worst −$1.0k → +$15.0k | kept |
| Equity | assigned blocks (new in the sim, below) | **`block_cut`**: a position jump bigger than our quotes can fill is an assigned block; cut it at once instead of skewing it off | **+$1.8k, t 2.6** / **+$1.6k, t 3.0** | **+$2.4k, t 3.2** / **+$0.9k, t 2.1** | kept |
| Liability | auction results | adaptive `competitive_margin` (+step on a fill, −step on a reject) | +$0.8k, t 1.6 / −$0.4k, t −1.9 | — | removed |
| ETF | USD balance from the ETF leg | hedge it back to flat with the USD security past a band | −$1.8k, t −1.5 / −$2.7k, t −1.8 | — | removed |

**Simulator corrections:**
* **Liability auction bug.** A competitive tender filled when our bid was *below* the client's
  reserve, so a lower bid always won. Officially a bid must be *past* the reserve (at or above it
  when the client sells). The reserve is now also random per tender (0–15 cents through the mid,
  own generator). The liability baseline (crowd learning off) drops from $42.1k to **$25.6k**
  (benign): the old number included near-free auction wins.
* **Equity block transfers** (DEVLOG item 6): about 3 times a heat, the case assigns an
  unannounced 5k–15k share block at the mid. The price then drifts 2–5 σ against the holder
  over 10 ticks. The equity baseline drops from $6.8k to **$1.9k** (benign). This assumes the real
  case does this. If the brief says it doesn't, set `blocks = False` in the sim state, and
  `block_cut` never triggers anyway.

**Liability crowd learning re-validated on the corrected auctions.** The first version (prior
0.06, weight 1.5, learning from every tender) cost **−$4.9k (t −3.0)** in the benign market:
its own unwind looked like a crowd, and the old auctions had hidden that. The fix is to learn
only from tenders not taken and charge only once the crowd is significant (`crowd_untaken_only`,
`crowd_gate`, prior 0, weight 1). Results: hostile **+$11.4k, t 3.9** / holdout **+$14.2k, t 3.0**;
benign −$0.3k (t −1.0) / holdout **$0**. Details in HOSTILE_MARKET.md.

| Case (16 seeds, benign / hostile) | Before today | Now |
|---|---|---|
| Liability | $25.6k / $8.6k (corrected auctions, crowd off) | **$25.2k / $19.9k** |
| Derivatives | $117.4k / $116.3k | **$143.8k / $142.3k** |
| Equity (with block transfers) | $1.9k / −$2.9k | **$3.6k / −$1.3k** |
| ETF | $35.2k / $47.7k | unchanged |
| Commodity | $393 / $394 | unchanged |

### Scoreboard with every adopted change (mean / worst seed, losing seeds)

| Case | Benign 1–16 | Benign 101–116 | Hostile 1–16 | Hostile 101–116 |
|---|---|---|---|---|
| Derivatives | $143.8k / $43.3k, 0 | $138.1k / $15.0k, 0 | $142.3k / $41.3k, 0 | $136.8k / $18.1k, 0 |
| ETF | $35.2k / $21.7k, 0 | $25.9k / −$1.5k, 1 | $47.7k / $23.2k, 0 | $47.9k / $6.5k, 0 |
| Liability | $25.2k / $14.2k, 0 | $24.0k / $7.6k, 0 | $19.9k / −$10.3k, 2 | $18.2k / −$13.1k, 3 |
| Equity | $3.6k / −$9.1k, 2 | $2.2k / −$6.4k, 4 | −$1.3k / −$8.2k, 11 | −$2.7k / −$9.3k, 13 |
| Commodity | $393 / $260, 0 | $363 / $252, 0 | $394 / $259, 0 | $378 / $281, 0 |

**Equity kill switch turned off** (`max_drawdown` 0). On seeds 1–16 it was +$1.05k (t 1.2) benign
and +$0.8k (t 1.1) hostile, so it was re-tested on 32 fresh seeds (17–48) with the rule fixed in
advance. Off vs $4k there: benign **+$951, t 3.0** (adj. p 0.011, 21/32), worst −$7.8k → −$3.3k,
CVaR −$4.9k → −$1.7k; hostile +$450 (t 1.2), worst and CVaR better. $6k: t 1.9 / 0.3. The stop
tripped on drawdowns from blocks and manipulation that revert, then flattened at the bottom.
Equity now: benign $4.7k (worst +$0.4k), hostile −$0.5k (seeds 1–16).

Also tried for equity and removed: pull the side an assigned block's drift would hit for 5/10
ticks (+$98 / +$27, t ≤ 1.4); pause a ticker after its inventory lost $500/$1,000 in 10 ticks
(−$0.1k to −$1.5k).

## 2026-10-08: hostile market (manipulative competitors) and crowd-aware tenders

The simulator's other traders were noise. In the real heat every team is a market maker,
and any of them can manipulate. `--hostile 1` (sim and tune) adds pump-and-dumps,
spoofing, liquidity vacuums, penny-jumping competitors, crowded tenders and competing ETF
arbitrageurs. `--hostile 0` (the default) is exactly the old market, so every earlier
baseline still stands. Full write-up: [HOSTILE_MARKET.md](HOSTILE_MARKET.md).

| Case | Benign → hostile, before defences (16 seeds) |
|---|---|
| Liability | $42.1k → $7.4k (t −6.1), 7 losing seeds |
| Equity | $6.8k → $1.5k (t −6.4), 6 losing seeds |
| Derivatives / commodity | no significant change |
| ETF | +$12.5k (arbitrage gains from dislocations) |

| Change | Hostile, 16 seeds | Holdout 101–116 (hostile) | Benign (1–16 / 101–116) |
|---|---|---|---|
| Liability crowd learning, first version (prior 0.06 $/10k, weight 1.5, adaptive 12-tick race). **Superseded**: these numbers used the old, buggy auction rules. The gated version above replaces it. | **+$33.7k, t 5.1, 16/16**; worst −$10.3k → +$12.9k; 0 losing seeds | **+$51.3k, t 7.2, 16/16**; worst −$13.1k → +$34.3k | +$3.3k (t 1.3) / +$1.4k (t 0.6) |

Removed (no improvement, code deleted; numbers in HOSTILE_MARKET.md and RESEARCH.md):
price band on aggressive orders, spoof-capped book depth, equity queue fighting,
toxicity-adaptive spreads, hold-on-dislocation, stronger equity skew under hostile, and an
equity kill switch re-tune (not significant).

## 2026-10-07: ETF tenders and a passive ETF leg (current ETF baseline)

The official Algo (ETF) case sends **private tender offers on RITC**. The simulator now
sends them too (fixed price near the market, 5k–30k units, every 20 ticks, open 15 ticks;
sizes and prices are a guess, the packages don't give them). They come from their own
random generator, so the price path is unchanged and the old baseline stays comparable.

| Change | 16 seeds (1–16) | Holdout (101–116) | Kept because |
|---|---|---|---|
| **Tenders** (`tenders = true`): accept a RITC tender when hedging it with the basket (walked on books ×2 for refill) clears `tender_min_edge` 0.02 after the basket's fees, and the package fits the risk room; leg repair trades the hedge, and the hedged block converges for free at the NAV close-out | **+$3.7k, t 3.26**, 12/16; worst $13.4k → $21.0k; p adj 0.016 (min edge 0 / 0.02 / 0.05 all t > 3) | **+$3.3k, t 3.24**, 13/16 | official case feature; better mean and tail |
| **Passive ETF leg** (`maker = true`, `maker_edge` 0.15): rest a RITC bid/ask at the price where a fill can still be hedged with the basket for 0.15/unit, earning the $0.01 rebate instead of paying $0.02 + spread | on top of tenders **+$1.3k, t 5.59**, 15/16; worst $21.0k → $21.7k (edge 0.10: +$1.4k, t 3.8; 0.20: +$0.6k, t 4.2; 0.02: −$1.4k) | **+$1.1k, t 3.41**, 14/16; worst −$2.4k → −$1.5k | better mean and tail on both seed sets |

| ETF | Mean | Worst | CVaR25 | Losing seeds |
|---|---|---|---|---|
| Before (official rules, 2026-10-06) | $30.2k | $13.4k | $17.9k | 0 |
| **Now** | **$35.2k** | **$21.7k** | **$24.9k** | 0 |
| Holdout 101–116, before → now | $21.4k → $25.9k | −$2.3k → −$1.5k | $6.6k → $8.0k | 1 → 1 |

**Equity:** the official-rules update didn't touch equity (same $6.8k, identical on every
seed). The older "$8.1k" was measured on 8 seeds under the old fill model, so it isn't a
drop. A re-tune of quote width, size and skew found nothing better (RESEARCH.md); the
config is unchanged. Equity's next gains need the real case brief or the practice case.

## 2026-10-06: official case rules applied

The simulator and configs now follow the official RITC case packages (2019/2020/2023),
as listed in [OFFICIAL_RULES.md](OFFICIAL_RULES.md). Derivatives and ETF numbers **are not
comparable** with the tables below: the market itself changed (fees, spreads, strikes,
news, currencies). Derivatives are now scored like the judges score them, NLV − delta penalty.

| Case | Mean | Worst | CVaR25 | Losing seeds | What changed |
|---|---|---|---|---|---|
| Derivatives | $117.4k | $32.8k | $49.8k | 0 | official fees/spread/strikes/news + parser fix |
| ETF | $30.2k | $13.4k | $17.9k | 0 | USD-quoted RITC, 2× limit weight, rebates, NAV close-out |
| Liability | $42.1k | $23.5k | $26.5k | 0 | none (compliance only) |
| Equity / Commodity | unchanged | | | | no official rule to apply |

Paired checks (16 seeds; holdout = seeds 101–116):

| Change | vs old setting | t | Holdout | Kept because |
|---|---|---|---|---|
| Vol-forecast parser reads *"between 27-30%"* | old parser ignored it. Without range news (`range_weight = 0`): **−$86.8k** | −7.44 | — | Official wording. Range news drives ~¾ of derivatives P&L |
| `option_fee` 1.00 → **2.00** (official) | $1 assumption: −$3.4k, worse on 14/16 | −3.84 | −$3.9k, t −3.94 | official rule and better |
| ETF `fx_ticker = "USD"`, `fx_mode = "divide"` | ignoring FX: −$7.4k, 2 losing seeds | −2.51 | −$4.7k, t −1.51 | official pricing equation (holdout not significant, but no worse anywhere) |
| Liability `decline_explicitly = true` | identical on all 16 seeds | 0.00 | — | official front-running rule, at zero cost |

Delta penalty, seeds 1–3: $85, $5, $0 per heat. The hedge band keeps delta well inside the limit.

**Read the derivatives level with care.** In the sim the forecast range always contains
next week's true vol and the market maker's vol lags, so reading the news pays very well.
The packages say the market maker's forecasts are "uninformed", which supports the direction.
The size of the edge must be checked in the RIT practice case.

## Previous baseline (fair backtest, 16 seeds, pre-official-rules)

Since 2026-10-05, passive fills in the simulator use common random numbers (see
[PERFORMANCE.md §5](PERFORMANCE.md)): every setting compared meets the same order flow.
The tables further down were measured on 8 seeds under the old sequential fills, so their
small differences are within noise. Use these numbers as the reference from now on.

| Case | Mean final NLV | Worst seed | Stdev |
|---|---|---|---|
| Liability | $42.1k | $23.5k | $11.3k |
| Derivatives | $18.1k | **−$14.0k** | $16.8k |
| ETF | $29.3k | $11.0k | $12.1k |
| Equity | $6.8k | $3.5k | $1.7k |
| Commodity | $393 | $260 | $59 |

The derivatives worst seed (+$0.3k on the old 8 seeds) was −$14.0k on 16 seeds. The cause
was the $40k kill switch itself, which flattened a temporary vol-arb dip at the bottom
(below).

### Final (after the 2026-10-05 risk review), 16 seeds

| Case | Mean final NLV | Worst seed | CVaR (worst 25%) | Losing seeds |
|---|---|---|---|---|
| Liability | $42.1k | $23.5k | $26.5k | 0 |
| ETF | $29.3k | $11.0k | $13.1k | 0 |
| Derivatives | **$21.7k** (was $18.1k) | **−$1.8k** (was −$14.0k) | **$5.5k** (was −$1.0k) | 1 (was 2) |
| Equity | $6.8k | $3.5k | $4.9k | 0 |
| Commodity | $393 | $260 | $312 | 0 |

### Every kept feature, switched off one at a time (paired vs baseline, 16 seeds)

| Case | Feature switched off | Effect of switching it off | Verdict |
|---|---|---|---|
| ETF | Executable exit (→ mid exit) | −$32.6k, t −11.0, 11 losing seeds | Confirmed |
| Liability | Block unwind (→ always cross) | −$11.9k, t −9.7 | Confirmed |
| Liability | Almgren–Chriss (→ old front-load curve) | −$3.3k, t −3.7, worst $23.5k → $19.0k | Confirmed |
| Liability | `min_profit_per_share` 0.01 (→ 0.03) | −$2.4k, t −3.0 | Confirmed |
| Commodity | News stop-loss | −$5, t −1.8 | Small, kept |
| ETF | Bertram thresholds (→ fixed) | −$0.5k, t −0.4 | Neutral; kept because it needs no tuning |
| Equity | Jump guard | −$96, t −0.7, worst seed $3.5k → $2.3k | Neutral mean, better tail; kept |
| Commodity | Bayesian impact (→ fixed, right prior) | $0 | Neutral; kept for robustness to a wrong prior |
| Derivatives | Parity arb | $0: never fires in the simulator | Kept for the real case |
| Derivatives | Hold-don't-trim (→ `exit_edge` 0.01) | +$7.1k, t 1.06, driven by one +$88k seed | Not significant; unchanged |
| Liability, commodity | Kill switch and throttle | $0: never trip | Insurance only |
| Equity | Kill switch and throttle | +$15, t 1.5 | ~free insurance |
| **Derivatives** | **$40k kill switch** | **+$3.6k, worst −$14.0k → −$1.8k; held-out seeds +$0.3k, never worse** | **Raised to $80k (catastrophe-only)** |

**Equity jump guard re-check:** +$96 vs off (SE $137, t 0.7). It is not a significant
gain in the mean, but it lifts the worst seed from $2.3k to $3.5k, so it stays on. The
earlier "$7.6k → $8.1k" was within noise.

## Final results per case

| Case | Starting point | Final |
|---|---|---|
| Liability | +$47.3k¹ / lock-step $31.6k (worst $16.9k) | **$37.6k (worst $22.5k)** |
| Derivatives | **−$20.3k**¹ | **$17.3k (worst +$0.3k)** |
| ETF | **−$0.5k**¹ | **$33.0k (worst $15.9k)** |
| Equity | +$1.3k¹ / lock-step $7.6k (worst $3.2k) | **$8.1k (worst $5.9k)** |
| Commodity | +$291¹ (worst −$244) | **$416 (worst $343)** |

¹ Measured on the old real-time simulator (6 seeds), before it was made reproducible. That
simulator's timing noise made results vary by up to $10k between identical runs, so only
the large moves (derivatives and ETF going from losing to winning) are meaningful from it.

## Added: kept because they improved results

### Infrastructure (all cases)

| Change | Why it matters |
|---|---|
| **Reproducible simulator.** Separate random generators for prices and fills, plus lock-step mode for `ritc tune`. | Same seed = same market for every setting compared. Before, equity runs varied by up to $10k from thread timing alone, so A/B tests were noise. |
| **News fetched with every price snapshot** | The bot never trades a repriced market against a stale headline. This was a major derivatives loss. |
| **Fill-based bookkeeping** (`Executor.filled`) | Hedges and strategy books count what filled, not what was sent. |
| **Package risk room** (`RiskManager.room_package`) | Multi-leg trades are sized against shared gross/net limits. Per-ticker checks let an ETF + basket trade use 3× the limit we thought. |

### Risk management (all cases)

| Change | Measured effect |
|---|---|
| **Graduated drawdown throttle**: new-risk sizes shrink from 50% of `max_drawdown`, kill switch at 100%; exits and hedges always run at full size | Losing streaks get smaller bets instead of an all-or-nothing stop |
| **Kill-switch levels** at ~1.5–2× the worst normal drawdown: liability $15k, derivatives $40k (now $80k, see above), equity $4k, commodity $200; ETF off on purpose | Close to free in normal runs. Set tighter, it hurt badly (derivatives $20k turned the worst seed from +$0.3k to −$14.7k). |

### Per case

| Case | Change | Measured effect |
|---|---|---|
| Liability | **Almgren–Chriss optimal execution** (λ = 3e-7, σ from GARCH, impact from book depth) | $34.1k → $37.6k, worst $16.9k → $22.5k |
| Liability | `min_profit_per_share` 0.03 → 0.01 (raised automatically in a drawdown) | $31.6k → $34.1k, same worst seed |
| Liability | Rejected competitive bids no longer counted as positions | Risk room stays free for the next tender |
| Derivatives | **Hold positions while the edge remains** (no trimming as IV converges) | Biggest single fix. Trimming was ~7,000 contracts of spread cost per run. |
| Derivatives | Delta hedge from **fills**, counting **every held option** (incl. near expiry) | Stopped ±6–8k share hedge flip-flops |
| Derivatives | Parity trades bounded by package risk room | No unbounded repeats |
| | *Derivatives combined:* | **−$20.3k → +$21.5k** (old simulator) |
| ETF | **Executable exit**: close when the reverse arb pays after costs, not when the mid premium hits zero | The old exit paid a full second set of costs; −$0.5k → +$16.8k (old simulator) |
| ETF | Complete a missed ETF leg while the premium still favours it; slippage budget from surplus edge | Fewer leg repairs at a loss |
| ETF | **Bertram optimal OU thresholds**, fitted live | Same mean as hand-tuned with no tuning; worst seed $10.4k → $15.9k |
| Equity | **Jump guard**: pull quotes for 2 ticks after a move > 4σ | $7.6k → $8.1k, worst $3.2k → $5.9k |
| Commodity | Carry book tracked from fills; spot re-hedged every loop; spot unwound when a future expires | Worst seed −$244 → +$309; no naked spot leg |
| Commodity | **News stop-loss** + `news_max_size` 40 → 60 | $368 → $409, better on 7 of 8 seeds |
| Commodity | **Bayesian learning of news impact** | Right prior: $409 → $416; wrong prior: $313 → $404 |

## Removed: tried, measured, didn't improve

| Change | Case | Result | Why removed |
|---|---|---|---|
| Whalley–Wilmott hedge band | Derivatives | Mean up to +$5.3k, but worst seed +$0.3k → −$4.7k | Same mean/stdev (1.16 vs 1.18): more risk, not a better trade-off |
| Vega cap (`max_vega`) | Derivatives | Mean $19.3k → $17.1k / $9.2k / $4.7k as the cap tightened | Cuts exposure exactly when the edge is largest |
| End-of-period inventory skew | Equity | $8.1k → $7.6k, worse worst seed | No inventory drift risk to remove in this market |
| Queue-aware tender pricing | Liability | Lower mean on 5 of 6 seeds | Declined profitable stacked tenders |
| Full Avellaneda–Stoikov quoting (spread and skew ∝ γσ²(T−t)) | Equity | −$1.3k to −$2.6k over 6 (γ, κ) settings, t −1.2 to −8.1; worst seed down to −$5.1k | Its skew fades to zero near the close, so inventory is carried to the end. The current constant skew is better. |
| Scenario-CVaR position limit (stress the delta-hedged book under spot × vol shifts; cap CVaR 25%) | Derivatives | $5k–$20k limits: mean −$1.6k to −$6.7k, worst seed −$1.8k → −$4.1k to −$7.3k | Exposure is largest exactly when the vol edge is; capping it cuts winners (same lesson as the vega cap) |
| VAMP / mid-price fair value instead of microprice | Equity | +$0.1k to +$0.6k, all t ≤ 1.0; mid's worst seed $2.8k vs $3.5k | No significant gain; simulator book sizes are random, so book-shape signals can't be validated here |

## Evaluated, never added

| Technique | Finding |
|---|---|
| Fourier periodogram (cycles) | No significant cycle in returns (p = 0.06–0.94). The only real one, commodity volatility every 40 ticks, is the scheduled report, which the news feed already times. |
| Fourier low-pass fair value | 4.5–17.6× worse than a random walk |
| Malliavin–Mancino Fourier volatility | Accurate on noisy trade prices (11% vs 107% error), but the bots use mid prices (0% error). Possible future use: the GARCH warm-up from price history. |
| Fourier (FFT/COS) option pricing | RIT uses Black–Scholes, which has an exact closed form |
| Copula portfolio optimisation | Simulator tickers are independent; one heat is too short to estimate tail dependence (±0.10 error); positions are hedged arbs or short-lived inventory |
| GLFT optimal quoting / order-flow imbalance | Needs realistic fill and order-flow data the simulator doesn't produce |
| Larger sizes: ETF `clip`, commodity `carry_clip`, derivatives 100 contracts | Lower mean, or a higher mean with a worse worst seed |

## Before the competition

1. Rescale the kill-switch levels (`[run] max_drawdown`) to ~1.5–2× the worst drawdown
   seen in the RIT practice case. Keep ETF at 0.
2. Recalibrate `ac_risk_aversion` (liability). It's in 1/$, so it depends on the case's scale:
   `python -m ritc tune liability --grid execution.ac_risk_aversion=...`
3. Bertram (ETF) and Bayesian impact (commodity) re-fit themselves live. Check their log
   lines (`BERTRAM`, `IMPACT`) in practice.
