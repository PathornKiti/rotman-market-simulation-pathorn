# Hostile market: surviving other teams

In the live competition the "other traders" are not noise. Every team runs a market
maker or an arbitrage bot, and any of them can push the price, show fake size or pull
its quotes. This page covers how the simulator models that, what it costs each bot,
which defences earned their place, and which didn't.

## The hostile simulator

`--hostile 1` turns on six kinds of competitor (`Market._adversary` and
`Market._hostile_books` in `src/ritc/sim/server.py`). With `--hostile 0`, the default,
the market is exactly the benign one: same seed, same prices, same scores (tested).

| Threat | What happens | Rate at `--hostile 1` |
|---|---|---|
| **Pump and dump** (momentum ignition) | The price runs 5–10 σ over 4–8 ticks, holds 0–2, then comes all the way back over 6–15 ticks. | 0.6% per ticker per tick (1–2 per ticker per heat) |
| **Spoofing** | A fake order 8–15 lots in size sits one tick behind the touch for 3–8 ticks. The crowd leans 1.5 σ that way, then it reverts when the order is pulled. **Our orders can never fill against it.** | 3% per ticker per tick |
| **Liquidity vacuum** | Market makers pull their quotes for 2–4 ticks. The mid is unchanged; the touch moves ≥ 3 spreads out and is thin. | 0.4% per ticker per tick |
| **Penny-jumping** | When our resting quote is at or inside the touch, a competing market maker steps one tick in front of it and gets the passive flow first. It never jumps through fair (it keeps a tick of edge to the mid). | 60% per tick per side we quote |
| **Crowded tenders** | Every desk gets the same block. Liability: three other desks unwind it into the same book (price runs 6e-6 × size against the unwind over 6–12 ticks, and half of the move stays). ETF: the crowd hedges in the basket. | every tender |
| **Competing arbitrageurs** (ETF) | Other desks close 70% of any ETF/basket gap a manipulation opens before we see it. | always |

Every draw comes from its own generators, keyed on the seed. Bot actions never change
them, so two settings compared on seed 3 meet the same manipulation. Penny-jumping
depends on where our quote is, so it is keyed on (seed, tick, ticker, side), like the
passive flow. `RITC_HOSTILE_THREATS="pump,spoof,vacuum,jump,crowd"` switches threats
on one at a time, for attribution.

```bash
python -m ritc sim liability --hostile 1                     # watch it
python -m ritc tune liability --hostile 1 --grid strategy.crowd_weight=1,1.5,2
```

**A change must pass in both markets.** A defence that wins only under `--hostile 1` and
costs money in the benign market is fitted to this model of the adversary, not to the
real competition.

## What the hostile market costs (before defences, 16 seeds)

| Case | Benign mean (worst) | Hostile mean (worst) | Difference | Losing seeds (hostile) |
|---|---|---|---|---|
| Liability² | $25.6k ($14.2k) | $8.6k (−$10.3k) | **−$17.0k, t −3.9** | 6 |
| Equity³ | $6.8k ($3.5k) | $1.5k (−$5.2k) | **−$5.4k, t −6.4** | 6 |
| Derivatives | $117.4k ($32.8k) | $116.3k ($33.7k) | −$1.2k, t −0.7 | 0 |
| Commodity | $393 ($260) | $394 ($259) | +$1, t 0.2 | 0 |
| ETF | $35.2k ($21.7k) | $47.7k ($23.2k) | +$12.5k, t 3.2 | 0 |

² Corrected auction rules (see CHANGES.md, "later" 2026-10-08), crowd learning off. Under the old,
buggy auctions it was $42.1k → $7.4k.
³ Before the simulator's equity block transfers were added. With them: $1.9k benign, −$2.9k hostile.

Damage by threat (each threat alone vs benign, 16 seeds; liability rows use the old auction rules):

| Case | Pump | Spoof | Vacuum | Penny-jump | Crowd |
|---|---|---|---|---|---|
| Equity | −$1.3k | −$0.7k | +$0.9k | **−$4.7k** | — |
| Liability, crowd learning off | −$3.6k | −$0.6k | +$2.1k | −$3.3k¹ | **−$33.5k** |
| Liability, crowd learning on (now) | −$2.3k | −$1.4k | +$2.0k | −$1.9k | **+$4.1k** |
| Derivatives | +$0.7k | +$0.6k | −$1.0k | 0 | — |
| ETF | +$7.5k | +$1.9k | −$1.0k | −$0.2k | +$2.3k |

¹ Measured before the penny-jumpers were made rational (they could jump through fair).

Vacuums *help* the bots that rest orders: the mid doesn't move, and their quote becomes the
only liquidity.

What this says:

* **Liability is exposed to the crowd.** A tender that looks profitable against today's book
  isn't, once three other desks sell the same block into it.
* **Equity loses its spread to competition.** Penny-jumpers take the passive flow at the
  touch; pumps run the market maker over in both directions.
* **Derivatives and commodity are robust.** Options are priced off the underlying the bot
  hedges with, and the commodity bot's trades are short and protected.
* **ETF arbitrage gains** from the 30% of every dislocation the other arbitrageurs leave.
  Don't count on that in the real heat: it depends on how fast the other desks are.

## Adopted

### Crowd learning for tenders (liability)

From every tender it sees and does **not** take, the bot measures how far the mid moves against
that tender's unwind over the next `crowd_ticks` (10). It keeps a conjugate normal estimate in
$ per 10k shares. Once the estimate is significant (posterior mean > 2 sd, `crowd_gate`), it
charges

```
crowd cost/share = crowd_weight × learned $/10k × tender size / 10k
```

in `evaluate_tender`, and the unwind races the crowd: the schedule shrinks from 30 to
`crowd_horizon_ticks` (12).

Final version, on the corrected auction simulator (paired vs crowd learning off):

| Setting | Hostile | Benign |
|---|---|---|
| learn from every tender, no gate (prior 0.06, weight 1.5: the first version) | +$11.5k, t 3.8 | **−$4.9k, t −3.0** |
| learn from every tender, no gate (prior 0, weight 1) | +$11.1k, t 3.8 | −$3.6k, t −2.6 |
| + skip tenders we took | +$11.6k, t 4.0 | −$2.8k, t −2.1 |
| + gate only | +$10.7k, t 3.7 | −$0.6k, t −1.0 |
| **+ skip taken + gate (adopted)** | **+$11.4k, t 3.9, 13/16** | −$0.3k, t −1.0 |
| Holdout seeds 101–116 (adopted) | **+$14.2k, t 3.0, 11/16** | **$0** (the gate never fires) |
| fixed 12-tick horizon instead of the adaptive race (old auction rules) | +$22.1k, t 4.6 | **−$7.6k, t −6.1** |

Two lessons came out of the benign column. First, our own unwind pushes the price against
us, which looks exactly like a crowd: learning from tenders we took double-counted impact the
book walk already prices. Second, a charge that isn't gated on evidence costs money whenever
there is no crowd. An earlier version with a 0.06 $/10k prior looked free in the benign market
only because the old simulator's broken auctions made up the difference.

The price of the gate: the first tender of a heat, before there's evidence, is unprotected
(hostile worst seed stays −$10.3k / −$13.1k).

## Tried and removed (they did not improve results)

| Defence | Idea | Result | |
|---|---|---|---|
| **Price band** on aggressive orders | Cap every IOC limit at the 7-tick median mid ± (2 × normal spread + 3 × tick σ), so no bot sweeps a vacuum or chases a pump | Commodity −$32, t −3.5 (both markets); derivatives worst seed $33.7k → $24.4k (hostile); liability −$0.6k; ETF +$0.3k (t 1.3); equity unchanged | Real moves get blocked as often as fake ones |
| **Spoof-capped depth** | Cap every book level at 3 × the median level size before microprice, imbalance, depth sizing and impact estimates | No measurable effect (±$50; equity hostile +$0.3k, t 1.2) | The bots size off the touch and walked VWAPs, so fake depth behind the touch barely enters |
| **Queue fighting** (equity) | When penny-jumped, step back in front while keeping 1–2 cents to the reservation price | −$2.6k to −$3.0k, t −2.8 to −3.0 (hostile) | The fills you win at a 1-cent edge are the toxic ones |
| **Toxicity-adaptive spread** (equity) | Markout of our own passive fills after 5 ticks; widen the side that keeps losing | −$0.9k, t −0.9 (hostile) | Markouts are noisy at this fill rate; widening also costs the good fills |
| **Hold on dislocation** (equity) | Don't cross to cut inventory while the price is pushed > 1.5 bands against it | Never triggered | The size taper keeps inventory below `hard_inventory` |
| **Stronger inventory skew** (equity, 6e-6) | Shed inventory faster | Hostile +$0.4k (t 1.0), worst −$5.2k → −$1.4k; **benign −$0.8k, t −2.7** | Costs money in the normal market |
| Ignore book imbalance (equity) | Spoofs move imbalance | Hostile +$0.3k (t 1.1); with stronger skew, benign −$1.0k (t −2.7) | Not significant |
| Other equity quoting under hostile | `min_half_spread` 0.03, `vol_mult` 2.5, `size` 1000, `max_inventory` 10k, `jump_sigmas` 2.5, `jump_pause_ticks` 5, `ou_weight` 0 | −$1.0k to +$0.1k, all \|t\| < 1.7 | Nothing beats the config |
| Equity kill switch re-tune | `max_drawdown` 2.5k / 6k / off vs 4k | 6k ≈ off: hostile +$0.4k (t 1.35), worst −$5.2k → −$2.0k; benign +$15 (t 1.5) | Later turned **off**: with block transfers, 32 fresh seeds gave +$951 (t 3.0) benign (CHANGES.md) |

Under the [fair-testing rule](PERFORMANCE.md), the code for every removed defence was deleted.
The rows above are the record.

## Protections already built in

These were already in the bots and matter more against hostile competitors than against noise:

| Threat | Protection | Where |
|---|---|---|
| Book moves between snapshot and order | Aggressive orders are marketable **limits** sized by walking the book, cancelled after one loop (IOC). No naked market orders. | `core/execution.py`, `core/book.py` |
| Fake depth | Fills are counted, not orders: legs, hedges and books are booked from what **filled** | `Executor.filled()` |
| Trading with yourself | Own orders are removed from every book before any decision | `OrderBook.from_api(exclude_trader=...)` |
| Pump / jump | Equity pulls its quotes for 2 ticks after a > 4 σ one-tick move | `jump_sigmas` |
| Showing your hand | Liability unwinds show at most `display_qty` (iceberg) and rest passively while on schedule | `core/algo.py` |
| Leg risk | Multi-leg trades go out concurrently, sized as whole packages; leg repair completes or squares a half-filled arb | `limit_many`, `room_package`, ETF `repair_legs` |
| Front-running rule | Tenders are declined explicitly, so trading the ticker is never "front-running" a pending tender | `decline_explicitly` |
| Runaway losses | Graduated drawdown throttle, then a kill switch that cancels, flattens and halts | `[run] max_drawdown` |
| Crash / Ctrl-C | Every resting order is cancelled on exit | `Runner.run` |
| A slow server (quote stuffing) | Loop latency is measured; a slow-loop warning fires above 500 ms | `LatencyStats` |

## In the heat

1. **Watch the `CROWD` log lines** (liability). They show the learned $/10k after each tender
   you didn't take. Once the mean passes 2 sd, the bot charges for the crowd and races it.
   If it stays near 0 after 5+ tenders, there's no crowd.
2. **Equity under competition earns less.** If the practice case shows penny-jumpers
   (your quote is rarely the best), don't fight on price. That was tested and loses.
3. **Never spoof or layer yourself.** The bots only send orders they are willing to have
   filled. Besides the rules, a fake order is free size for anyone who can hit it.
4. If the practice case shows a crowd from the very first tender, a prior (`crowd_prior_mean`,
   in $ per 10k shares) protects that first block, but it costs money when there is no crowd.

## Caveats

* The adversaries are a model. Rates, sizes and shapes are guesses, chosen to be plausible
  and visible in one heat. The adopted defence learns its one key number (crowd impact)
  live, so it doesn't depend on the guess. Everything else was required not to cost money
  in the benign market.
* The ETF gain under `--hostile` is partly a modelling choice (competitors close 70% of
  a gap). Read it as "arbitrage is robust to manipulation", not as extra profit.
* Penny-jumpers here are memoryless and never get adversely selected themselves.
  Real ones learn your quoting pattern, so equity in the real heat may be worse than
  this simulation.
