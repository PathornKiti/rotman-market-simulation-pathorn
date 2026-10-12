# Practice heats on the real server (2026-10-11)

Five Liquidity Risk heats on the practice server (161.200.68.42), the bot running `ritc run liability --live` on
Windows next to the RIT client. Source files in `docs/practice_logs/`: `log1.txt`, `log2.txt`, `log3.txt`, `log4.txt`,
`stable_but_loss_other_trader_log.txt` (bot console logs). All five ran **before** the fixes below.

What the text logs do not contain: final P&L per heat, fines, and the close-out. Those are in RIT's Trader Info and
Transaction Log, and in the run journals `logs\liability-*.jsonl` on the Windows machine (copy them here and run
`ritc report` for the per-tick P&L path and per-tender counterfactuals).

## 1. The five heats

| Heat | Fixed tenders: taken / seen | Auctions: won / bid | Blocks unwound | Past zero (speculation) | Notes |
|---|---|---|---|---|---|
| log1 | 1 / 3 (+303 accepted **by hand** in the UI) | 0 / 3 | TAME ×2 | TAME +2,100, −2,200, **+10,800** | tenders 103/203/303 answered in the UI before the bot's turn |
| log2 | 4 / 7 | **1** / 4 (CROC 19.13) | CRZY, CROC ×2, TAME ×2 | TAME +1,200, +2,000 | 2 fixed declined only for risk room (one blocked by an auction we then lost) |
| log3 | 4 / 7 | 0 / 4 | CRZY, CROC ×2, TAME | **CRZY −20,200**, ±1,100 | duplicate orders at the end of the CRZY unwind |
| log4 | 4 / 7 | **1** / 4 (CRZY 9.48) | CROC, CRZY ×2, TAME | **CROC +9,800** | CROC unwind lost $1.76/share: liquidity dried up, then a late cross |
| last | 1 / 7 | 0 / 4 | CRZY | none | ~**$40k**; the top trader made ~$200k. 2 tenders declined for room (one blocked by a lost auction) |

Speculation fines at the **new** schedule ($0.20/share up to 5,000, **$0.60** past it), from the positions in the
logs: log1 ~$7k, log2 ~$0.6k, log3 ~$11k, log4 ~$4k, last $0. **Cause: real RIT books a new order asynchronously**,
so for a moment it is in neither the open-orders list nor the position, and the bot re-sent the same unwind order
(pairs 20-250 ms apart). **Fixed** (`execution.order_settle = 1.0`, `core/execution.py`), not yet on Windows.

## 2. Tenders

- **11 per heat**, about one every 30-35 s, rarely overlapping (the old simulator assumed 34).
- **50,000-59,000 shares** each (the last of a heat 17-34k): one block is half the 100k net limit.
- **Windows 30-31 ticks.** Fixed tenders: ids 3, 103, …, 603; auctions (`price = None`): ids 700-703.
- **Edges are wide**: the bot's model profit/share ranged −$0.87 to +$0.78. The 14 accepted fixed tenders had
  +$0.15 to +$1.00/share against the price our resting unwind started at (average ~+$0.55, ~$29k per block).
- Of the 14 fixed tenders declined on price, 7 were clearly bad (−$0.22 to −$0.87/share in the model, still
  negative after adding back the model's ~$0.19 pessimism, section 4). The other 7 were marginal: −$0.11 to
  +$0.001 in the model, so +$0.08 to +$0.19 under the liquidity model. They were small, uncertain gains, not the gap.

## 3. Auctions: won 2 of 18, root cause

Wins are read from the positions (a block appears after the window): log2 #702 (CROC) and log4 #702 (CRZY).

**Mechanical cause: the bid sits far from the mid because five haircuts are stacked on it.** The bot bids the price
that leaves `competitive_margin` after its estimated exit: `bid = exit − fee − risk/drift − margin`. On the
calibrated simulator (129 bids, `auction_rootcause` study):

| Part of the bid's distance from mid | $/share |
|---|---|
| exit priced by walking 2× the thin visible book, not at our resting touch | 0.095 (real heats: ~0.17) |
| risk premium + adverse-drift charge | ~0.10 |
| `competitive_margin` | 0.05 |
| commission | 0.02 |
| half-spread we earn by resting | −0.025 |
| **total** | **0.24 from mid** (real heats: 0.18-0.68 where a price was nearby) |

The client's hidden reserve is closer to the mid than that, so the bid is "declined" at once. Our two real wins were
bids 0.18 and 0.35 from mid: real reserves are sometimes generous, and we only win those.

**But bidding closer does not pay.** Pricing auctions from forward liquidity (`liquidity_model = "auctions"`)
brings the bid to 0.14 from mid and the win rate from 1% to 14%, and loses money:

| 32 seeds each | auctions-only flow pricing, margin 0.05 | margin 0.02 |
|---|---|---|
| calibrated, design 301-332 | +$6.7k (t 1.8) | +$4.2k (t 1.1) |
| calibrated, holdout 401-432 | **−$4.1k** (t −1.6) | **−$7.3k** (t −2.6) |
| calibrated + hostile crowd | −$3.9k, worst heat −$11k → −$56k | −$7.8k, worst −$59k |
| old default simulator | +$1.9k (t 1.6) | +$1.8k (t 1.1) |

Why: an auction won near the reserve is a ~$0.10/share trade on a 55k block that takes half the 100k net limit while
it unwinds, and every team got the same block (in a crowd, all of them sell it at once). A fixed private tender
is worth ~$0.50/share. Extra wins 6 → 21 per 32 heats cost about three fixed tenders and lost on the rest.

**Where the loss comes from** (post-trade replay, 32 holdout heats, realized P&L per tender): the extra auction
wins made money on their own (+$52k, ~$6k a block, 8 of 21 losing), but fixed tenders booked fell 113 → 108 and
fixed-tender P&L fell $184k. Each won block fills half the net limit while it is held, and a same-direction fixed
tender (worth ~$27k) no longer fits.

**Winning only when it costs no room** (`auction_max_net`: bid up only if |net after the win| ≤ 45k, e.g. an auction
that offsets what we hold): no loss anywhere (holdout −$37, hostile $0) but no gain either (96 fresh seeds −$0.8k,
t −1.4). Not adopted.

**Verdict:** losing most auctions is not what separates $40k from $200k. Bidding to win them is a near-zero-edge
trade with a crowd tail. What would change this is evidence that real reserves are usually generous (our two wins
at 0.18 and 0.35 from mid hint at it). The run journal records the mid at every bid, so 2-3 more practice heats of
journals give the real reserve distribution; then bid just inside it.

## 4. Liquidity: the main finding

Your professor's point: liquidity, looked at **forward** (how much the market will take while we unwind), matters
more than volatility. The logs measure it directly. The bot rests its unwind at the touch and the market fills it:

| Heat | Block | Seconds | Shares/s absorbed | Price move while unwinding (+ = our way) |
|---|---|---|---|---|
| log1 | TAME −53,000 | 14 | 3,664 | −0.04 |
| log2 | CRZY −54,000 | 17 | 3,177 | +0.07 |
| log2 | TAME +52,000 | 31 | 1,655 | +0.14 |
| log2 | CROC +53,000 | 56 | 946 | +0.10 |
| log3 | CRZY +55,000 | 62 | **893** | **−0.83** |
| log3 | CROC −54,000 | 43 | 1,263 | +0.05 |
| log3 | CROC +53,000 | 16 | 3,258 | +0.41 |
| log3 | TAME −51,000 | 16 | 3,255 | −0.52 (last 15 s of the heat) |
| log4 | CROC −53,000 | 76 | **701** | **−1.76** |
| log4 | TAME −55,000 | 18 | 2,986 | +0.02 |
| log4 | CRZY −51,000 | 18 | 2,796 | +0.16 |
| last | CRZY +55,000 | 23 | 2,404 | +0.07 |

1. **The real market absorbs 50k blocks in 15-25 s at ~2,500-3,600 shares/s** (TAME fastest, CROC slowest and most
   variable, as the brief says: TAME "medium-high", CROC "varied"), with the price barely moving. The bot prices a
   tender by walking ~2× the thin visible book, which undervalued the tenders it took by **~$0.19/share** on
   average. That pessimism is why its auction bids lose.
2. **Both big unwind losses were liquidity failures, not volatility**: the only two slow unwinds (701 and 893
   shares/s) are the two big adverse moves. When nobody trades on our side, the price is running away from our
   resting orders.

What was built and tested (`strategy.liquidity_model`):

- `"flow"`: a forward liquidity forecast per stock and side from time & sales (EWMA, started at the practice
  rates 2,600 / 3,200 / 1,500 shares/s), and tender pricing from it: exit = the touch we rest at, unwind time =
  shares ÷ forecast flow, risk premium = `risk_aversion` × σ × √time (thin stock → longer → dearer). `LIQ` log line
  every 60 s.
- The simulator got a practice-calibrated market, `RITC_STRESS=real=1` (these vols, tender sizes / timing / edges,
  and resting-order absorption). Under the current bot it reproduces the real heats: 6% auction wins (real 11%),
  2.6 fixed tenders taken per heat (real 1-4).

| Test (32 seeds each, calibrated market) | Effect of `"flow"` |
|---|---|
| design seeds 301-332 | +$7.1k/heat (t 1.9), 13 better / 0 worse; auction wins 3 → 8 of 67 |
| **holdout** 401-432 | +$1.1k (t 0.3): **not confirmed** |
| old default simulator | +$8.8k (t 3.2) |
| hostile market (other desks dump the same block) | −$3.3k, worst heat −$11k → −$56k |

| **fresh seeds 701-764 (64)** | **+$5.7k (t 2.9)**, 28 better / 1 worse; hostile +$1.2k (t 0.6) but worst heat −$52.8k → −$61.1k, losing heats 12 → 16 |

Over all 128 calibrated seeds: about **+$4.9k a heat**. It is the higher-mean, fatter-tail choice ("play to
win" against expert bots); the defaults are the protect-the-worst-heat choice.

**Verdict: the AGGRESSIVE PROFILE for the graded heats, decided by the scoring rule and practice heat B**
(PRACTICE_PLAN.md). Default stays `liquidity_model = "book"`. Valuing the exit at our resting price is right
when nobody else unwinds the same block; when a crowd dumps it, the old pessimism protected us. Turn it on for a
heat with `--set strategy.liquidity_model=flow` if the practice shows no crowd (the `CROWD` lines stayed ≈ 0 in 4 of
5 heats).

## 5. Why ~$40k and not ~$200k, and what to do

- **Variance and different tenders.** The brief: private tenders differ across teams in timing, price and size.
  In the last heat the bot saw one good fixed tender; on the calibrated simulator the same bot ranges $0-$210k a
  heat (sd ~$46k). One heat's leader is not a fair benchmark. Holding a 50k block through a 2.5-dollar TAME
  slide is +$125k for someone and −$125k for someone else.
- **Fines and bad unwinds we did cause** (fixed or fixable): ~$4-11k a heat of speculation fines from duplicate
  orders (fixed); room blocked by auctions we then lost (~$15k when it happens, 2 of 5 heats).
- **Answer timing barely matters now**: on the calibrated market answering 3 s before expiry instead of at once is
  +$4k (t 0.9, not significant). Answering at once (your choice, `decide_late_ticks = 0`) stays.
- **Auctions are not the easy upside** (section 3): bidding to win them loses money on holdout seeds and in a
  crowd. Learn the real reserve from the journals first.

## 6. Next practice heat: what to record

1. Copy `logs\liability-*.jsonl` and the matching `.log` for every heat (P&L path, tender mids, our bids vs mid).
2. Screenshot Trader Info (final NLV) and the Transaction Log (fines, close-out at the bell).
3. Run one heat with `--set strategy.liquidity_model=flow` and compare the auction win rate.
