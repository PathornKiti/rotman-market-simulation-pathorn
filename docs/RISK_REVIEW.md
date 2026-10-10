# Risk review: why losing heats lose, and what to do about it

Liquidity Risk Case (2027 selection), `src/ritc/strategies/liability.py` with `config/liability.toml` as of
2026-10-10 (hold to the bell, `hold_risk_budget = 40000`, crowd learning on, kill switch off).

Scope: diagnosis and design only. Nothing under `src/`, `config/` or `tests/` was changed. Prototypes of the
proposed mitigations were written as **scratchpad-only monkeypatches**, only to size the expected effects. The
quant analyst re-implements them as config flags and confirms them on fresh seeds (201+).

## 0. Summary

* **In the brief's market (benign simulator) the bot does not lose.** Over 32 seeds (1-16 and 101-116), 0
  heats lost and the worst heat made +$22.1k. In the volatility ×2, 10c-worse-tenders and 2×-size stresses,
  1 heat in 32 lost, by $0.2k to $2.0k. Those are random-walk draws on held inventory and are not worth buying
  protection against (section 8).
* **Every material loss is a crowded-tender loss.** Other desks get the same block and unwind it into the
  same book. In the hostile and "everything at once" stresses, the losing heats lose to:
  1. **Own-crowd under-pricing on the thinnest stock.** The crowd estimate is pooled across the three stocks,
     but a crowd moves CRZY (thin) about 1.5× the pooled average and TAME (deep) about 0.6×. CRZY blocks are
     under-charged, and the stacked 50k-75k CRZY blocks are the worst heats.
  2. **Holding through a crowd move you can see coming.** `hold_risk_budget` and the 90 s bell hold set the
     ticker passive whatever the crowd evidence says, so `crowd_horizon_ticks` (the race) rarely gets to act.
     Inventory sits still while the crowd of our own tender, or of a same-direction tender we declined, runs
     the price against it.
  3. **Late acceptance into a crowd.** A block accepted at tick 387-410 (valued at the free close-out by
     `hold_valuation`) has its own crowd ramp land exactly at the bell, with no time for the transient half to
     revert.
* Pumps, spoofs, vacuums, fines, close-out and passive adverse selection are second-order. None of them
  decides a losing heat.
* **Top mitigations, all gated on live crowd evidence** (so the benign heat is untouched). The package
  M1 + M2 over 32 seeds:
  * hostile: P(loss) 12% → 0%, CVaR25 +$0.1k → +$10.3k, mean +$5.8k;
  * everything at once: P(loss) 25% → 6%, worst −$105k → −$63k, CVaR25 −$22.8k → +$6.0k, mean +$21.6k;
  * benign: unchanged (section 2b).

  The mitigations:
  1. **Crowd race on adverse arrival**, including inside the bell window: hostile +$4.8k (t 2.2) and +$8.2k
     (t 2.6); everything-at-once +$8.2k and +$16.9k; benign about $0.
  2. **Per-ticker crowd impact with partial pooling**: combined with (1), everything-at-once +$18.6k (t 3.0)
     and +$24.6k (t 2.3), worst −$105k → −$63k and −$27.5k → +$21.4k.
  3. **Gate hygiene is operational, not code.** The crowd gate opens falsely in 15 of 32 benign
     volatility ×2 heats (3/32 in base), but that costs little, and every fix tried delays real detection
     (hostile worst −$4.5k → −$21k). Instead, set `crowd_prior_mean` from the practice rounds if they show
     a crowd.
  4. **Optional insurance:** a crowd-conditional position cap. Everything-at-once worst −$63k → −$1k, but
     −$4k to −$22k mean, so off by default.
* **Rejected with data**: static per-stock caps (−$17k to −$28k in the 2×-size market, t −4.2 / −4.8); the
  soft drawdown throttle (hostile worst −$7.4k → −$43k); a smaller or zero hold budget (−$0.5k to −$5.0k in
  the benign market); a blunt global crowd weight (−$7k to −$11k in hostile, t −1.9 to −2.6).
* **Simulator caveat that matters for the real heat.** In the hostile simulator, tenders are priced off the
  *true* mid while crowd residuals permanently displace the *visible* mid. That hands the bot a "displacement
  edge" worth +$92k per hostile heat on average (+$300k "everything at once"). This is an artifact. When
  tenders are anchored to the visible price, as a real server pricing off the current market would, the
  hostile mean drops from +$36.5k to about **+$1.4k with P(loss) 25%**. **If the real heat has a crowd, expect
  thin profits and take few tenders.** Section 6.

---

## 1. Method

### 1.1 Exact P&L attribution

Every change in V = cash + Σ position × mid is booked to exactly one bucket. Score = Σ buckets − fines. The
identity is checked on every run, and the residual is $0.00 on every run.

| Bucket | Definition |
|---|---|
| `e_true` | Tender edge at booking vs the **true** mid: sign × q × (mid − price), by kind (private / auction / winner-take-all) |
| `e_disp` | Extra edge because the **visible** mid at booking was displaced from the true mid by manipulation (other tenders' crowd residuals, pumps, spoofs). Hostile simulator only, and an artifact (section 6). |
| `e_ownc` | The part of our own tender's crowd move that happened **before booking** (auctions / WTA booked at expiry, booking delay) |
| `unwind` | Crossed fills: slippage vs the mid before the fill + taker fees + our own impact on the remaining inventory |
| `maker` | Resting fills vs the mid before the fill. Negative when the price jumped through a stale resting order. |
| `rw_early` / `rw_budget` / `rw_bell` | Random-walk drift on inventory: on schedule (crossing allowed) / passive by the hold budget / last 90 s |
| `pump`, `spoof` | Inventory × the manipulation offset change |
| `ownc_early`, `ownc_bell` | Inventory × the crowd move of **tenders we hold**, before / inside the bell window |
| `othc` | Inventory × the crowd move of **tenders we did not take** (or lost) |
| `close` | Close-out at the last price (± half spread) vs the mid |
| `fines` | Speculation / front-running fines |

Diagnostics: `mk5` is the 5-tick markout of resting fills (adverse selection). `dd` is the maximum intraday
drawdown of score. `riskpk` is the peak Σ\|pos\| × σ × √(ticks left), using the simulator's true σ.

### 1.2 Scenarios and seeds

The ten scenarios of `stress.py`. "all" = hostile + vol 1.5 + depth 0.6 + edge −0.05 + size 1.5 + booking 2
+ strict front-running. Seeds 1-16 and 101-116, 32 per scenario. Two scratchpad-only sensitivity variants
re-anchor every new tender (price, reserve, rival) to the visible mid: `hostile@a` and `all@a` (section 6).
**Seeds 201+ were not used; they are reserved for the quant's confirmation.**

Scripts (session scratchpad,
`/private/tmp/claude-502/-Users-pathorn-kit-rotman-market-simulation-pathorn/8f6fe7ff-5ce3-46b0-b85c-322c31b3b682/scratchpad/`):

* `riskattr.py`: the attribution harness.
* `tab2.py`: the tables in this document.
* `abx.py`: paired A/B with bucket deltas.
* `proto.py`: mitigation prototypes and the anchoring variant.

## 2. Risk metrics per scenario (current config, 32 seeds)

| Scenario | Mean | SD | Worst | CVaR25 | P(loss) | Max DD avg | Max DD max | Peak inv. risk avg | Peak inv. risk max | Mean/SD |
|---|---|---|---|---|---|---|---|---|---|---|
| base (brief) | 45,806 | 15,899 | 22,118 | 26,483 | 0% | 7,704 | 15,371 | 25,055 | 41,789 | 2.88 |
| hostile | 36,477 | 30,001 | −7,396 | 59 | 12% | 28,687 | 63,492 | 24,908 | 53,994 | 1.22 |
| volatility ×2 | 25,923 | 16,305 | −1,980 | 5,176 | 6% | 10,701 | 26,651 | 33,736 | 70,654 | 1.59 |
| thin books ×0.5 | 34,265 | 14,157 | 7,133 | 16,283 | 0% | 7,632 | 18,907 | 20,878 | 38,523 | 2.42 |
| tenders −10c | 9,578 | 7,874 | −208 | 1,780 | 3% | 4,326 | 12,488 | 11,775 | 25,280 | 1.22 |
| tenders 2× size | 46,124 | 23,247 | −1,358 | 17,218 | 3% | 10,437 | 44,754 | 28,010 | 63,473 | 1.98 |
| tenders 2× as often | 88,545 | 26,805 | 22,410 | 54,154 | 0% | 13,233 | 44,191 | 35,695 | 59,220 | 3.30 |
| booking delay 3 s | 47,030 | 15,761 | 21,434 | 27,360 | 0% | 7,709 | 18,010 | 25,291 | 45,596 | 2.98 |
| strict front-running | 43,539 | 15,258 | 20,222 | 24,402 | 0% | 7,848 | 15,371 | 25,055 | 41,789 | 2.85 |
| everything at once | 78,932 | 96,637 | −104,979 | −22,764 | 25% | 72,583 | 210,908 | 42,300 | 100,301 | 0.82 |
| hostile, tenders anchored to visible | 1,403 | 5,804 | −13,616 | −5,048 | 25% | 6,628 | 25,139 | 11,760 | 41,789 | 0.24 |
| everything, anchored | −1,257 | 4,408 | −19,366 | −5,423 | 12% | 2,810 | 23,138 | 5,778 | 36,090 | −0.29 |

Peak inventory risk is in $ of 1-sd move to the bell. Rows 1-10 reproduce `final_stress.txt` exactly; the
simulator is deterministic per seed. In the anchored rows the bot books 2.8 tenders per hostile heat
(vs 12.7 unanchored) and 0.3 in "everything at once": with the displacement artifact gone, it correctly
takes almost nothing.

What the table says:

* The benign stresses have mean/SD of 1.6 to 3.3 and essentially no left tail.
* Hostile and "everything at once" are the only scenarios where the left tail is material. Their max
  drawdown is 4× to 9× the benign one.
* "Peak inventory risk" is not what separates winners from losers. In the benign market it reaches $42k and
  nothing bad happens. The crowd, not σ√T, is the risk the hold budget does not see.

### 2b. The same metrics with the recommended package (M1 + M2 prototypes), 32 seeds

| Scenario | Mean | SD | Worst | CVaR25 | P(loss) | Max DD avg | Max DD max | Peak inv. risk avg | Mean/SD | Δ mean vs config |
|---|---|---|---|---|---|---|---|---|---|---|
| base | 45,670 | 15,894 | 22,118 | 26,483 | 0% | 7,704 | 15,371 | 25,055 | 2.87 | −136 (1 false-gate seed) |
| hostile | 42,235 | 26,636 | **3,780** | **10,263** | **0%** | 21,230 | 52,850 | 24,178 | **1.59** | **+5,758** |
| vol ×2 | 26,667 | 15,629 | −1,980 | 6,048 | 9% | 10,716 | 26,651 | 33,998 | 1.71 | +744 (one more −$1.3k loser) |
| thin | 34,265 | 14,157 | 7,133 | 16,283 | 0% | 7,632 | 18,907 | 20,878 | 2.42 | 0 |
| edge −10c | 10,076 | 8,056 | −208 | 1,780 | 3% | 4,420 | 12,488 | 11,964 | 1.25 | +498 |
| size ×2 | 46,124 | 23,247 | −1,358 | 17,218 | 3% | 10,437 | 44,754 | 28,010 | 1.98 | 0 |
| 2× often | 88,435 | 26,883 | 22,410 | 53,872 | 0% | 13,194 | 44,191 | 35,695 | 3.29 | −110 |
| booking 3 s | 46,922 | 15,762 | 21,434 | 27,360 | 0% | 7,709 | 18,010 | 25,291 | 2.98 | −108 |
| strict FR | 43,402 | 15,232 | 20,222 | 24,402 | 0% | 7,848 | 15,371 | 25,055 | 2.85 | −137 |
| everything | 100,540 | 95,744 | **−63,087** | **5,989** | **6%** | 57,804 | 164,885 | 42,923 | 1.05 | **+21,608** |
| hostile, anchored | 3,137 | 6,036 | −10,388 | −2,468 | 25% | 6,390 | 25,139 | 12,727 | 0.52 | +1,734 |
| everything, anchored | −1,164 | 4,465 | −19,366 | −5,352 | 12% | 2,810 | 23,138 | 5,778 | −0.26 | +93 |

The benign scenarios are unchanged to within one false-gate seed. The crowded scenarios gain mean and lose
tail: hostile P(loss) 12% → 0%, everything 25% → 6%, everything CVaR25 −$22.8k → +$6.0k. These are
in-sample for the seeds the prototypes were sized on. **Confirm on 201+.**

## 3. P&L attribution

### 3.1 Mean attribution by scenario ($ per heat, 32 seeds)

| Scenario | e_true | e_disp | e_ownc | unwind | maker | rw_early | rw_budget | rw_bell | pump | spoof | ownc_early | ownc_bell | othc | close | fines | mk5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 46,850 | 0 | 0 | −27 | −2,098 | −801 | −90 | 1,762 | 0 | 0 | 0 | 0 | 0 | 120 | 0 | 200 |
| hostile | 12,010 | 92,393 | −19,862 | −931 | −2,994 | −26 | −583 | 816 | −527 | 189 | −21,898 | −8,769 | −12,457 | 64 | 0 | −2,903 |
| vol ×2 | 30,618 | 0 | 0 | −213 | −5,350 | 49 | 716 | 697 | 0 | 0 | 0 | 0 | 0 | 121 | 0 | 396 |
| thin | 35,305 | 0 | 0 | −9 | −2,160 | −498 | 53 | 1,469 | 0 | 0 | 0 | 0 | 0 | 159 | 0 | −60 |
| edge −10c | 9,964 | 0 | 0 | 0 | −505 | −548 | −290 | 561 | 0 | 0 | 0 | 0 | 0 | 106 | 0 | 76 |
| size ×2 | 44,311 | 0 | 0 | −19 | −1,578 | 1,117 | 525 | 2,001 | 0 | 0 | 0 | 0 | 0 | 292 | 0 | 136 |
| tenders ×2 often | 91,974 | 0 | 0 | −27 | −3,416 | −382 | −1,241 | 186 | 0 | 0 | 0 | 0 | 0 | 236 | −25 | 112 |
| booking 3 s | 47,723 | 0 | 0 | −9 | −2,089 | −1,692 | −751 | 2,962 | 0 | 0 | 0 | 0 | 0 | 136 | 0 | 162 |
| strict FR | 46,850 | 0 | 0 | −27 | −2,098 | −801 | −90 | 1,762 | 0 | 0 | 0 | 0 | 0 | 120 | −2,267 | 200 |
| everything | −22,541 | 300,411 | −74,916 | −11,012 | −6,842 | 736 | −607 | −340 | −48 | 11 | −54,717 | −20,908 | −27,264 | 103 | −1,396 | −4,282 |

(`rw_early` includes `rw_budget`.)

Reading it:

* **Benign heats.** P&L is the tender edge (`e_true`). Drift nets to about zero, as a random walk should.
  Unwinding costs almost nothing, because the bot barely crosses: hold to the bell plus the budget. Passive
  fills give back $1.6k-5.4k (`maker`, stale orders jumped through), which is the price of not crossing.
  Close-out is a few hundred dollars.
* **Hostile heats.**
  * Our own crowds cost **−$50.5k per heat** (`e_ownc + ownc_early + ownc_bell`).
  * Other tenders' crowds cost **−$12.5k** on held inventory.
  * Pumps cost −$0.5k and spoofs +$0.2k.
  * The displacement artifact (+$92k) more than pays for all of it.
* **Everything at once.** The same pattern ×3: own crowd −$150k, others −$27k, displacement +$300k.
* **Others' crowds lose on average (−$12.5k) instead of netting to zero, for two reasons.**
  * **Resting exits are short optionality.** When a crowd pushes the price toward our resting exit, the
    exit fills and we leave early. When it pushes the price away, nothing fills and we carry the full
    position through the move. Measured over the crowd ramp: 32% (hostile) / 38% (everything) of the
    position is unwound during favourable ramps, but only 19% / 27% during adverse ones. Arrival direction
    is about even: 48% of other-tender crowds start against us.
  * **Selection, under realistic anchoring.** With tenders anchored to the visible price, 73% of
    other-tender crowds that start while we hold run against us: 27 vs 10 episodes, $26k vs $7k at the
    permanent half. The bot takes tenders that offset its inventory and declines same-direction ones, and
    the declined same-direction tender is exactly the one whose crowd unwinds against what we hold.

### 3.2 Every losing seed (current config), with contrast

| Scen | Seed | Score | e_true | e_disp | e_ownc | unwind | maker | rw_early | rw_budget | rw_bell | pump | spoof | ownc_early | ownc_bell | othc | close | fines | dd | late |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hostile | 103 | −7,396 | 9,330 | 47,601 | −1,081 | 0 | −828 | −9,124 | −4,168 | −688 | −563 | 1,200 | −13,527 | −560 | −37,775 | 0 | 0 | 39,109 | 0 |
| hostile | 104 | −7,294 | 28,001 | 154,555 | −13,929 | −3,375 | −8,161 | −5,822 | −9,659 | −4,866 | −8,115 | 1,055 | −73,468 | −12,276 | −57,290 | −80 | 0 | 63,492 | 2 |
| hostile | 7 | −4,536 | 9,054 | 44,569 | −10,681 | −4,093 | −1,665 | −490 | −7,558 | −1,298 | −3,021 | 840 | −19,045 | −453 | −17,954 | 0 | 0 | 56,670 | 1 |
| hostile | 113 | −4,077 | 12,730 | 58,421 | −20,699 | 0 | −1,339 | 272 | −2,112 | −1,243 | −530 | 0 | −13,404 | −26,957 | −12,087 | 1,000 | 0 | 28,309 | 1 |
| *hostile median* | 10 | 37,979 | 6,035 | 134,666 | −41,264 | 0 | −1,672 | 559 | −2,826 | 9,303 | 0 | 262 | −26,477 | −12,738 | −30,323 | 0 | 0 | 50,292 | 2 |
| *hostile best* | 114 | 107,253 | −38,932 | 200,631 | −46,608 | 0 | −2,697 | 1,230 | 774 | 8,907 | −204 | −875 | −3,239 | −46,358 | 34,599 | 1,605 | 0 | 12,320 | 5 |
| vol ×2 | 103 | −1,980 | 12,656 | 0 | 0 | 0 | −1,298 | −13,368 | −2,549 | 30 | | | | | | 0 | 0 | 16,092 | 0 |
| vol ×2 | 8 | −414 | 16,512 | 0 | 0 | 0 | −3,035 | −15,069 | −1,173 | 1,178 | | | | | | 0 | 0 | 21,435 | 2 |
| *vol ×2 median* | 4 | 26,466 | 49,613 | 0 | 0 | −2,733 | −10,207 | −12,030 | −668 | 1,823 | | | | | | 0 | 0 | 26,497 | 2 |
| edge −10c | 11 | −208 | 343 | 0 | 0 | 0 | −446 | −745 | −333 | 640 | | | | | | 0 | 0 | 1,787 | 1 |
| size ×2 | 3 | −1,358 | 9,693 | 0 | 0 | 0 | −741 | −10,311 | −7,719 | 0 | | | | | | 0 | 0 | 6,502 | 0 |
| *size ×2 median* | 107 | 44,105 | 38,540 | 0 | 0 | 0 | −1,818 | 9,263 | 8,068 | −1,801 | | | | | | −79 | 0 | 4,504 | 3 |
| everything | 7 | −104,979 | −27,070 | 343,250 | −122,386 | −39,164 | −7,093 | −21,705 | −1,420 | −1,474 | −6,562 | −1,156 | −124,160 | −1,441 | −95,728 | 0 | −288 | 210,908 | 1 |
| everything | 103 | −27,542 | −13,941 | 163,908 | −14,786 | −9,730 | −5,076 | −6,609 | 5,094 | −3,139 | 1,643 | 1,811 | −42,881 | −1,212 | −93,898 | 0 | −1,000 | 80,851 | 1 |
| everything | 113 | −15,405 | 6,687 | 162,000 | −66,375 | −7,265 | −586 | 1,526 | −216 | 690 | 0 | 0 | −23,581 | −90,000 | 0 | 1,500 | 0 | 89,310 | 1 |
| everything | 15 | −13,185 | −2,058 | 123,150 | −35,979 | 0 | −4,135 | 1,446 | 2,288 | −18,494 | −7,187 | −133 | −2,723 | −41,918 | −24,543 | −611 | 0 | 112,426 | 2 |
| everything | 3 | −10,592 | −6,292 | 57,639 | −4,683 | 0 | −3,348 | −13,536 | −6,811 | 0 | −998 | −57 | −10,223 | 0 | −25,959 | 0 | −3,000 | 18,825 | 0 |
| everything | 10 | −6,356 | −90,432 | 259,826 | −37,736 | −5,675 | −3,596 | 8,527 | −2,858 | −3,057 | 0 | 123 | −62,806 | 4,282 | −75,670 | 0 | −144 | 133,723 | 1 |
| everything | 14 | −3,534 | −2,837 | 51,299 | −10,500 | −3,902 | −1,976 | 6,279 | 6,783 | 153 | −4,168 | −141 | −18,643 | 9,095 | −29,058 | 300 | 0 | 40,527 | 1 |
| everything | 101 | −523 | −86,100 | 385,336 | −61,033 | −25,945 | −5,331 | −5,891 | −3,132 | 7,291 | 1,485 | −1,281 | −76,873 | −55,591 | −56,224 | −1,434 | −1,990 | 62,545 | 3 |
| *everything median* | 105 | 40,403 | 5,634 | 146,911 | −8,148 | −1,780 | −7,501 | 14,037 | 14,465 | 5,197 | 4,459 | 185 | −44,483 | −12,095 | −63,805 | −116 | 0 | 40,401 | 1 |
| *everything best* | 114 | 278,741 | −181,450 | 561,480 | −88,771 | 0 | −5,354 | 290 | −654 | −10,493 | 679 | −322 | −4,613 | −118,363 | 129,172 | 1,897 | −3,000 | 44,725 | 4 |
| *base worst (winner)* | 116 | 22,118 | | | | | | | | | | | | | | | | | |

`late` = tenders booked after tick 330, inside the bell window. Blank cells are 0 (no manipulation in that
scenario).

### 3.3 Root cause of each losing seed

| Seed | Loss | Which tenders / stock | Threat | Late acceptance? | Oversized? | Held through it? | Root cause |
|---|---|---|---|---|---|---|---|
| hostile 103 | −$7.4k | 9 tenders, peaks TAME 35k, CRZY 30k | crowd (others' −$37.8k, own −$15.2k) | no | no | **yes**: passive under the budget | Inventory left resting while later same-ticker tenders' crowds ran against it |
| hostile 104 | −$7.3k | 4 CRZY BUYs stacked at t197-229 (10k+30k+20k+10k, peak 64k) plus three 50k CROC blocks | crowd (own −$99.7k, others −$57.3k); a CRZY pump (+0.12..0.18) inflated the visible price at acceptance (pump −$8.1k) | 2 | **yes**: CRZY 64k on the pooled crowd charge (≈0.05 $/10k vs CRZY ≈0.12) | yes | Under-priced own crowd on the thinnest stock, stacked; accepted into a pump |
| hostile 7 | −$4.5k | CRZY 50k BUY (t136); CROC auction booked at −0.57/share vs mid (t355) | crowd (own −$30.2k, others −$18.0k) | 1 (auction) | CRZY 50k | yes | CRZY block under-priced; auction booked after its own crowd ramp |
| hostile 113 | −$4.1k | CRZY 50k BUY at **t410**: pooled charge 0.33/share vs visible edge 0.44 | own crowd at the bell **−$27.0k** | **yes** | 50k CRZY | bell hold | Late acceptance: the crowd ramp landed on the bell |
| vol ×2 103 / 8 | −$2.0k / −$0.4k | 3 / 7 ordinary tenders | none: random walk (−$10.8k / −$13.9k while unwinding on schedule, −$13.4k / −$15.1k in all) | no | no | no (crossing on schedule) | Noise. A ~3-sd draw against a $12.7k / $16.5k edge. |
| edge −10c 11 | −$0.2k | 2 thin-edge tenders | none | 1 | no | – | Noise around zero edge |
| size ×2 3 | −$1.4k | TAME 40k (t223), CROC 20k | none: random walk on held TAME (−$7.7k passive by budget) | no | no | yes (budget) | Noise. About −1 sd on a held 40k block. |
| everything 7 | **−$105.0k** | CRZY 75k BUY (t136): charge 0.79/share vs realised crowd 1.5 peak / 0.75 residual. CRZY 75k auction (t188), peak CRZY 89k. | own crowd **−$248.0k**, others −$95.7k, impact −$32.5k | 0 | **yes**: 3× the CRZY max order, twice | partly on schedule: the crowd outruns a 30-tick schedule | Own-crowd under-pricing on CRZY (pooled estimate), stacked |
| everything 103 | −$27.5k | 8 tenders, peaks CRZY 45k / TAME 40k / CROC 30k | others' crowd **−$93.9k**, own −$58.9k | 1 | no | **yes** (budget: −$80k of crowd while passive) | Held inventory through others' crowds |
| everything 113 | −$15.4k | CRZY 75k BUY at **t410** (charge 0.82 vs ~1.5) | own crowd at the bell **−$90.0k** | **yes** | 75k | bell hold | Late acceptance plus pooled under-pricing |
| everything 15 | −$13.2k | CROC 75k BUY at **t387**; CRZY WTA booked at t414 at −0.88/share vs mid | own crowd at the bell −$41.9k, before booking −$36.0k | **yes** (2) | 75k | bell hold | Late acceptances; a WTA booked after its own crowd |
| everything 3 | −$10.6k | 5 tenders | others' crowd −$26.0k, strict fines −$3.0k | no | no | yes | Held through others' crowds |
| everything 10 / 14 / 101 | −$6.4k / −$3.5k / −$0.5k | many | own −$96.3k / −$20.0k / −$193.5k; others −$75.7k / −$29.1k / −$56.2k | 1 / 1 / 3 | yes in 101 | yes | Same mix |

**Contrast.** The hostile and everything-at-once *winners* have the same structure as the losers: a big
displacement edge and a big own-crowd cost. What separates them is mostly the sign of `othc`. In hostile 114
(+$107k) other tenders' crowds happened to run *with* our inventory (+$34.6k); in everything 114 (+$279k)
by +$129k. Holding inventory through crowds is a coin flip with a negative mean (selection) and a huge
variance. That is the reducible part.

**Losers in the anchored (realistic-pricing) variant.**

| Seed | Score | What happened |
|---|---|---|
| hostile@a 116 | −$13.6k | Others' crowd −$12.4k: three same-direction CRZY tenders at t36-41, before the crowd gate had any evidence (n = 1) |
| hostile@a 101 | −$10.1k | Random walk −$10k, own crowd −$7.6k, others −$7.8k |
| hostile@a 104 | −$9.9k | Others' crowd −$8.0k, own crowd −$5.1k |
| everything@a 101 / 2 | −$19.4k / −$15.8k | One to three early tenders, taken before the gate opened |

The mechanism is the same: crowd. Part of it is unprotectable: tenders before the gate opens.

### 3.4 Pumps, spoofs, vacuums: not the problem

* Pumps cost −$0.5k per heat on average (worst −$8.1k, hostile 104, where a CRZY pump coincided with
  stacked buying).
* Spoofs are about +$0.2k.
* Vacuums do not appear in the drift: the mid is unchanged. They help the resting orders.
* The hold-to-the-bell design already does the right thing with a pump: it does not sell into it, and the
  pump reverts fully.

Recommendation: no pump- or vacuum-specific defence. One was tried in `HOSTILE_MARKET.md` (price band: −$0.6k
liability) and rejected. A pump-triggered "stop holding" would sell into a temporary dislocation, which is
the wrong way round.

## 4. Root causes (named)

1. **RC1. The pooled crowd estimate under-charges the thin stock.** `BayesImpact` is one number in $ per 10k
   shares for all three stocks. Crowd impact scales with 1/depth: in the simulator 0.12 (CRZY), 0.045 (TAME)
   and 0.075 (CROC) $/10k at hostile 1, pooled about 0.08. So the bot charges a 50k CRZY block about 0.33
   $/share against a true peak of 0.6, and over-charges TAME (it loses good TAME blocks). The charge is
   quadratic in size (per-share cost ∝ q), so the error is largest exactly on the 50k-75k CRZY blocks that
   make the worst heats (hostile 104, 7, 113; everything 7, 113). In the real heat the brief's own liquidity
   table (CRZY medium-low, TAME medium-high, CROC varied) says the same thing will happen.
2. **RC2. The hold logic is blind to a predictable crowd.** `hold_within_budget` (and the bell window) set a
   ticker passive whenever \|pos\| × σ_GARCH × √T ≤ $40k. They ignore `crowded()`, so the crowd race
   (`crowd_horizon_ticks = 12`) is overridden whenever the position is within budget, which is most of the
   time. When a new tender arrives on a ticker we hold, and its crowd will unwind *against* our position (our
   own accepted block, or a declined same-direction one), the price move over the next 6-12 ticks is
   predictable, and we sit still. Buckets: hostile `ownc_early` −$21.9k, `othc` −$12.5k per heat (of which
   −$12.9k and −$7.5k accrue while passive by budget).
3. **RC3. Late acceptance into a crowd.** `hold_valuation` values a late block at the free close-out minus a
   σ√T premium and the pooled crowd charge. A block accepted with fewer ticks left than the crowd ramp (6-12
   ticks) plus its reversion (~40 ticks) takes the whole peak at the bell, and the bell window forbids racing.
   Hostile `ownc_bell` −$8.8k per heat; everything 113: −$90k from one tender.
4. **RC4. No protection before the gate opens.** The crowd gate needs 1-3 untaken tenders of evidence. Tenders
   in the first ~30-60 s are unprotected (hostile@a 116, everything@a 101 and 2). Irreducible without a prior.
5. **RC5. Random-walk drift on held or unwinding inventory** (vol ×2, size ×2, edge −10c). Small: ≤ $2k
   losses, 1 seed in 32. These are the cost of holding for free, and irreducible at zero mean cost (section 8).
6. **Not root causes:** pumps, spoofs, vacuums (3.4); close-out (+$0.1k); maker adverse selection (−$2k to
   −$7k per heat, steady, not tail); fines (strict reading only, −$2.3k mean, worst −$9.0k: resting fills in
   the same tick a new tender appears, before the bot can see it and decline; ≤ one iceberg child each).

## 5. Ranked mitigations

M1-M3 are **gated on live crowd evidence** (`crowded()`: learned crowd impact > 2 posterior sd). M4 is an
operational calibration. With no
crowd the gate stays shut and the bot runs exactly as now: full size, full hold. Prototype numbers are paired
differences vs the current config on seeds 1-16 / 101-116. **They are estimates to be re-confirmed on 201+.**

### M1. Crowd race on adverse arrival (including inside the bell window)

* **Mechanism.** Once the crowd gate is open, every new tender on ticker X predicts a price move of direction
  d = −1 for a BUY tender (the crowd sells) and +1 for a SELL tender, over the next ~6-12 ticks. After our
  decision on it, compute pos_X (position + pending bookings). If pos_X × d < 0, set `race_until[X] = now + K`.
  This covers both our own accepted block and a declined tender whose crowd will hit what we already hold.
  While racing:
  * X is removed from the hold-budget passive set **and from the bell-window passive rule**;
  * X's block is re-worked to a K-tick schedule (the usual Almgren-Chriss path, `max_slippage` and
    `participation` still cap each slice);
  * after K ticks the normal hold logic resumes for whatever is left.
* **Trigger.** `crowded()` and pos_X × d < 0. Never while X is frozen by our own open auction / WTA bid
  (respect_windows). The bot already declines explicitly, which closes the window first.
* **Config flags.** `execution.crowd_race = true`, `execution.crowd_race_ticks = 12`,
  `execution.crowd_race_in_bell = true`.
* **Measured (prototype).**

  | Scenario | Seeds 1-16 | Seeds 101-116 | Worst seed |
  |---|---|---|---|
  | hostile | +$4.8k (t 2.19, 12/16) | +$8.2k (t 2.63, 11/16) | −$4.5k → −$2.1k / −$7.4k → +$8.4k |
  | everything | +$8.2k (t 1.43, 13/16) | +$16.9k (t 2.24) | −$105k → −$85k / −$27.5k → +$14.0k |
  | anchored hostile | +$0.3k | +$0.8k | n.s., the gate opens too late there |
  | benign base | −$0.3k (1 seed with a false gate) | $0 | unchanged |

  Where the money comes from: `othc` +$8k to +$17k and `ownc_bell` +$2.5k to +$5k, against `unwind` −$7k to
  −$12k.
* **Ablations.**
  * **The bell part is essential.** `no_bell` loses it all: hostile −$0.8k / −$1.3k.
  * Own-only and others-only each get about half; use both.
  * K = 20 ≈ K = 12 (everything +$18.7k vs +$18.6k), but −$0.9k on the false-gate benign seed. Keep 12.
* **Expected cost in normal heats:** about $0. The race fires only when the gate is open. Across the 8 benign
  stresses × 32 seeds, the per-scenario mean change of the M1 + M2 package is −$0.14k to +$0.74k (section 2b).
* **Falsify.**
  * On seeds 201-232: if hostile or everything is not ≥ +$2k with t ≥ 2, or the worst seed / CVaR25 is not
    better, drop it.
  * If any benign scenario shows t ≤ −2, drop it.
  * Log `RACE` events: in benign heats they should be ~0. If they are not, the gate is false-firing (see M4).
* **Implementation notes.**
  * `LiabilityStrategy.handle_tenders`: after each decision (accept or decline) on a tender while
    `self.crowded()`, compute `pos = snap.positions[t] + self.pending_delta(t, now)` (this already includes
    an accepted block). If `pos * (-1 if action == "BUY" else 1) < 0`, set `self.race_until[t] = now + K`.
  * `unwind_block`:
    * after `self.algo.passive` is computed, discard racing tickers from it;
    * if `self.algo.blocks[t].deadline > now + K`, re-work the block (`start_pos = pos`, `start_tick = now`,
      `deadline = now + K`), or call `self.algo.work(...)` with the K-tick deadline.
  * `BlockExecutor.step` (`core/algo.py`): `passive_only` is currently
    `t in self.passive or now >= self.passive_from`. Add a per-ticker override set (e.g.
    `self.no_passive_from`) so a racing ticker may cross inside the bell window.
  * Log `RACE`.
* **Compliance note.** A race only *reduces* a position that came from an accepted tender. The brief:
  "only trades that reduce an existing position from an accepted tender are permitted without penalty". The
  new tender is already declined (window closed), or is our own accepted block. Under the strict
  front-running reading, fines went *down* by $0.4k-0.7k. If in doubt, confirm with the case organisers that
  reducing inventory after declining a tender is not treated as front-running.

### M2. Per-ticker crowd impact with partial pooling

* **Mechanism.**
  * Keep the pooled `BayesImpact` (it drives the gate).
  * Store each untaken tender's (x = q/10k, y = adverse move) per ticker.
  * For ticker X: β_X = (μ_pool/τ² + Σ x·y / σ_n²) / (1/τ² + Σ x² / σ_n²), with τ = 0.05 $/10k (between-stock
    spread) and σ_n = `crowd_noise_sd`. This shrinks to the pooled mean when X has little data.
  * The tender's crowd charge becomes `crowd_weight × β_X × q/10k` instead of the pooled mean, only when the
    gate is open (as now).
* **Trigger.** Same gate as now. Nothing changes without a crowd.
* **Config flags.** `strategy.crowd_per_ticker = true`, `strategy.crowd_ticker_tau = 0.05`.
* **Implementation notes.**
  * In `_learn_crowd`, next to `self.crowd.update(x, move)`, append `(x, move)` to `self.crowd_obs_by[t]`.
  * Give `crowd_cost(qty)` a `ticker` argument, called from `handle_tenders`, returning
    `crowd_weight * max(0, beta(ticker)) * qty / 10_000`, where `beta()` is the shrinkage formula above with
    μ_pool = `max(0, self.crowd.mean)`.
  * Keep `crowded()` on the pooled estimate.
  * Log β per ticker in the `CROWD` line.
* **Measured.**

  | Scenario | M2 alone, seeds 1-16 | M2 alone, seeds 101-116 | M1 + M2, seeds 1-16 | M1 + M2, seeds 101-116 |
  |---|---|---|---|---|
  | anchored hostile | +$1.4k (t 2.03) | +$1.5k (t 1.42) | +$1.7k (t 1.85) | +$1.8k (t 1.66) |
  | everything | +$3.3k | +$13.5k (t 1.88); worst −$105k → −$60k | **+$18.6k (t 3.03)**; worst −$105k → −$63k; CVaR25 −$33.8k → −$12.5k; P(loss) 5 → 2 | **+$24.6k (t 2.25)**; worst −$27.5k → +$21.4k; P(loss) 3 → 0 |
  | hostile (unanchored) | −$0.8k (n.s.) | −$1.3k (n.s.) | +$4.7k (t 1.98) | +$6.8k (t 1.50) |

  In the unanchored hostile market, M2 alone gives up displacement edge (an artifact, section 6) on CRZY
  tenders it now declines.
* **Expected cost in normal heats:** $0. All eight benign stresses are identical or within −$0.3k.
* **Falsify.**
  * In hostile logs the per-ticker estimates must rank CRZY > CROC > TAME. In the real heat, by observed book
    depth.
  * On 201+: the paired gain in anchored hostile must be > 0 with t ≥ 2 over 32 seeds, and benign must be
    unchanged.
  * If β_X estimates in the practice case are noisy (posterior sd > mean), increase τ toward 0 (back to
    pooled).

### M3. Crowd-conditional position cap (optional insurance, not a default)

* **Mechanism.** While `crowded()`, decline a tender that would take \|position\| in its stock above
  `crowd_max_position` (prototype: 50k shares). Reducing tenders are always allowed.
* **Measured, on top of M1 + M2:**

  | Scenario | Seeds 1-16 | Seeds 101-116 |
  |---|---|---|
  | everything | −$4.3k vs M1+M2, but worst −$63k → **−$1.1k**, CVaR25 −$12.5k → +$6.5k, mean drawdown $59k → $28k | **−$22.3k** vs M1+M2 (t 0.2 vs config) |
  | hostile | −$1.1k | −$0.2k |
  | base | −$1.6k on the 1 seed with a false gate | $0 |
  | anchored | $0 | $0 |

* **Verdict.** A pure tail-for-mean trade, and only in the extreme-size stress whose dollar magnitudes are a
  model choice (section 6). Not a default. Keep it as a switch for the real heat, if the practice rounds show
  both a crowd **and** blocks of 50k+ in a thin stock.
* **Config flags.** `strategy.crowd_max_position = 0` (off) / 50000.
* **Falsify.** On 201+, everything at once: worst must improve by > $30k and the mean must not fall by more
  than ~$5k. Otherwise leave it off.

**Late-acceptance guard: tried and rejected.** While crowded, with < 30 ticks left, require edge to cover 2×
the crowd charge. On top of M1 + M2: hostile +$0.3k / −$1.5k, everything +$0.0k / **−$10.9k**, anchored
hostile −$0.2k / −$1.1k. M2's per-ticker charge already prices the late CRZY blocks that lost (everything
113: the 75k CRZY at t410 is declined under M1 + M2, and the seed goes from −$15.4k to +$38.4k). M1's
in-bell race handles the rest.

### M4. Gate hygiene: operational, not a code change (tested)

Everything above keys on `crowded()`, so its error rates matter.

* **False gates in a volatile benign market are common.** The gate opens in 3/32 benign base heats, but in
  **15/32 volatility ×2 heats**. Two causes:
  * `crowd_noise_sd = 0.10` is a fixed $ noise, so twice the volatility looks like evidence.
  * Our own unwinding leaks into the estimate through declined same-direction tenders.
* **The cost of a false gate is small.** In vol ×2, removing the false gates is worth only +$0.7k to +$3.1k
  per heat (n.s.). M1 + M2 are robust to them: +$1.4k / +$0.1k in vol ×2 with the false gates still there.
* **The fixes tried slow down real detection.**

  | Fix | vol ×2 (false gates) | hostile | anchored hostile |
  |---|---|---|---|
  | uniform `crowd_noise_sd = 0.2` | 15 → 1 false gates, +$0.5k / +$1.5k | −$2.3k / −$7.3k | worst −$13.6k → −$37.5k |
  | volatility-normalised noise (GARCH σ × √`crowd_ticks` per observation), with or without dropping observations where we traded the stock | 15 → 2 false gates | alone −$3.5k / +$0.3k, worst −$4.5k → −$20.8k; on top of M1 + M2 about the same mean but worse worst seeds (+$4.2k → −$22.6k, +$3.8k → −$6.6k) | alone $0 / −$0.4k, worst −$13.6k → −$17.3k |

  The hostile damage comes from GARCH σ itself being inflated by crowd and pump moves.
* **Recommendation (operational, for the real heat):**
  * Keep the gate as it is.
  * In the practice rounds, read the `CROWD` lines. If the learned $/10k is clearly positive across rounds,
    set `crowd_prior_mean` to that value and `crowd_prior_sd` to about half of it. That closes the
    before-the-gate gap (RC4).
  * If the practice case shows no crowd (estimate near 0 after 5+ tenders), leave the prior at 0. A prior
    with no crowd costs money (HOSTILE_MARKET.md: −$4.9k).
* **Falsify.** Count benign heats with an open gate on 201+. If M1 + M2 lose more than ~$1k per heat in vol ×2
  because of false gates, revisit the vol-normalised gate with a higher `c`.

## 6. Simulator caveats that change the conclusions

* **Tender anchoring (the big one).** `_tick_liability` prices tenders, reserves and rivals off the true mid
  (manipulation offsets removed). Crowd residuals are permanent (`residual = 0.5`, never decays). So after a
  few crowded tenders the visible mid, which is where we exit and where the bell closes us out, sits far from
  the price tenders are quoted against.
  * The bot sees "edges" of 0.4-1.9 $/share and books them: `e_disp` +$92k per hostile heat, +$300k
    everything.
  * This is why the hostile mean (+$36.5k) looks almost benign, and why "everything at once" (+$78.9k) beats
    the base market.
  * With tenders anchored to the visible price (`proto.anchor`), hostile is **+$1.4k, P(loss) 25%** and
    everything is −$1.3k. There the bot correctly declines nearly every tender.
  * **Recommendation to the quant:** add this as a simulator option (e.g. `RITC_STRESS=anchor=1`). Judge
    every crowd-related change in both modes. A blunt crowd-weight increase "fails hostile" only because it
    gives up displacement edge; in the anchored mode it would look different.
* **Crowd size.** amp = 3 × impact × q × hostile, with impact scaled by 1/depth and q by size. In
  "everything at once", a 75k CRZY tender moves CRZY 15% ($1.50 on $10). The −$105k tail is mostly this model
  choice. Read "everything at once" for *mechanisms*, not dollar magnitudes.
* **Stale-order fills.** Resting orders that the new tick's price jumps through fill at their limit. That is
  the `maker` bucket (−$2k to −$7k). Real RIT queues may be kinder or harsher; check in practice.

## 7. Rejected or de-prioritised directions (with data)

| Direction | Probe (existing knobs or prototype) | Result | Verdict |
|---|---|---|---|
| Hold budget shrinks with vol / smaller budget | `hold_risk_budget` 20000 / 0 | base −$0.5k / −$4.3k (t −1.6 / −2.9), holdout −$0.7k / −$5.0k; hostile +$0.2k / −$1.7k, +$1.3k / +$1.9k | Costs in normal heats. Vol losses happen while crossing on schedule, not while holding (vol ×2 seed 103: −$10.8k on schedule vs −$2.5k passive by budget). The $ budget already shrinks the held size when σ rises. **No.** |
| Blunt crowd charge | `crowd_weight` 1.5 / 2.0 | hostile −$8.2k / −$10.8k (t −2.5 / −2.6), holdout −$7.0k / −$7.2k; everything +$6.4k / −$2.9k, −$4.7k / −$12.2k; worst everything −$105k → −$35k | Right direction for own-crowd, wrong instrument: punishes TAME, gives up displacement. M2 is the targeted version. |
| Soft drawdown throttle | `max_drawdown` 60000, soft start 0.25 | base 0; hostile −$0.05k / −$2.2k with worst −$7.4k → **−$43k**; everything −$27.8k / −$40.5k (t −1.8 / −2.5) | Drawdowns here are crowd transients that half-revert; throttling locks them in and forfeits later edge. Same lesson as the kill switch. **No.** |
| Static per-stock cap | gross 60k per stock (config limit groups: `risk.groups.<name>` with one ticker) | base −$3.4k / −$2.0k; **size ×2 −$17.4k / −$28.3k (t −4.2 / −4.8)**; hostile −$1.7k / 0; everything −$2.7k / −$7.7k but worst −$105k → −$10.6k | Cuts the extreme tail but costs real money whenever tenders are big and the market is fine. Only a crowd-conditional cap is admissible, and even that is insurance only (M3). |
| Time-of-day taper (unconditional) | (not run: data already in RESEARCH.md) | `hold_valuation` for late tenders is +$3.5k to +$7.2k in benign | An unconditional taper gives that back. **No.** |
| "Lean": stop resting the exit while a favourable crowd ramp runs (mirror of M1) | prototype on top of M1+M2 | hostile −$0.7k / +$0.1k, everything +$3.5k / −$3.6k, anchored +$0.1k / +$0.1k | Fixes the passive-exit asymmetry of 3.1 in principle, but nothing measurable. **No.** |
| Crowd-conditional late-acceptance guard | prototype: < 30 ticks left, edge must cover 2× crowd charge | on top of M1+M2: hostile +$0.3k / −$1.5k, everything 0 / −$10.9k | Redundant once M2 prices per ticker and M1 races in the bell. **No.** |
| Pump / vacuum stop-holding | (attribution) | pumps −$0.5k per heat, vacuums ~0 | Nothing to win; holding through a pump is right. |
| Concentration across the three stocks | (attribution) | stocks are independent; losses are single-stock crowd events | Net-limit shadow price already tried and removed (RESEARCH.md). **No.** |

## 8. Honest limits

* **The benign tail is not reducible at zero cost, and does not need to be.** 0 of 32 losing heats; the 1-in-32
  small losses in vol / size / edge stresses are 1-3 sd random-walk draws on positive-edge inventory. Crossing
  to avoid them costs $0.5k-5k per heat in every heat.
* **The first crowded tenders of a heat are unprotected** until the gate opens (RC4), unless a practice-based
  prior is used.
* **A crowd that moves faster than we can exit is not fully avoidable.** The ramp starts the tick the tender
  appears, and our own exit has impact. In hostile, M1 recovers most of `othc` (+$8k to +$11k of −$12.5k)
  but only about a third of `ownc_bell` (+$2.5k to +$3.7k of −$8.8k).
* **Winner-take-all and auction blocks are booked at window end.** Their own crowd has already moved (`e_ownc`
  −$20k hostile), and we cannot trade the stock while our bid is open, so M1 cannot race them. Only pricing
  (M2) helps there. This is the residual worst seed after M1 + M2 (everything 7, −$63k): M2 declines the
  first 75k CRZY block (t136), but the bot still wins a 75k CRZY auction at t188 and is frozen for its bid
  window while the crowd runs. A crowd-aware auction margin (bid shading ∝ β_X × q) is the next idea to try
  if this matters in practice. It is untested.
* **The dollar magnitudes of the hostile and everything stresses are model choices** (section 6). The
  mechanisms are what transfers to the real heat. In a real crowded heat with tenders priced off the current
  market, expect the bot to take few tenders and make little. That is correct behaviour, not a bug.
* **Overfitting guard.** None of the mitigations uses a seed, a ticker name, a tick or a size threshold fitted
  to the losers. They key on the learned crowd estimate, the sign of position vs the predicted crowd
  direction. Prototypes were sized on seeds 1-16 and 101-116 only; **the
  quant must confirm on 201-232 (and ideally 301-332) in base, all 8 benign stresses, hostile, everything,
  and both anchored variants.**

## 9. Test protocol for the quant

1. Implement each mitigation as a config flag, default off. Compare on/off paired on seeds 201-232 in:
   base, vol ×2, thin, edge −10c, size ×2, 2× often, booking 3 s, strict, hostile, everything, hostile
   anchored, everything anchored.
2. Keep a mitigation only if:
   * benign scenarios show no t ≤ −2;
   * hostile and everything show paired t ≥ 2, OR worst and CVaR25 are better with the mean not worse
     (t > −1);
   * anchored hostile is not worse.
3. Order: M1 first (largest, cleanest); then M2 on top; M3 only as an off-by-default switch. M4 needs no
   code: it is a practice-round calibration.
4. Log lines to add for the real heat: `RACE <ticker> until <tick> (pos, crowd dir)`, per-ticker β in the
   `CROWD` line, `CROWD CAP` / `LATE CROWD` declines.

## Validation on 201-232 (quant analyst, 2026-10-10)

The mitigations were implemented as config flags (`execution.crowd_race` / `crowd_race_ticks = 12` /
`crowd_race_in_bell`, `strategy.crowd_per_ticker` / `crowd_ticker_tau = 0.05`, `strategy.crowd_max_position`).
They follow sections 5 M1-M3. The simulator option `RITC_STRESS=anchor=1` replaces `proto.anchor`; it gives the
same numbers (everything@a seed 203: -$38,212 in both). All runs below are paired on fresh seeds 201-232. "Diff"
is vs the current config (t over 32 seeds). RACE = number of `RACE` log lines over the 32 heats.

| Scenario | Config mean / worst / CVaR25 / losing | M1 | M1 + M2 | M2 alone | M3 alone (50k) |
|---|---|---|---|---|---|
| base | 41,392 / 11,418 / 16,416 / 0 | -39 (t -0.5), 6 RACE | -39 (t -0.5) | 0 | 0 |
| vol ×2 | 24,290 / -72 / 3,970 / 2 | +108 (t 0.5), 44 RACE (3 false gates) | -3 | +128 | +25 |
| thin ×0.5 | 31,325 / 733 / 11,576 / 0 | +349 (t 1.0) | +349 | 0 | – |
| edge -10c | 10,676 / -3,846 / 1,048 / 3 | 0 | 0 | 0 | – |
| size ×2 | 37,431 / -1,689 / 10,413 / 1 | +126 | +126 | +550 | +655 |
| 2× often | 80,821 / 24,735 / 48,250 / 0 | 0 | -158 (t -1.0) | -158 | 0 |
| booking 3 s | 42,524 / 11,430 / 16,069 / 0 | -157 (t -1.3) | -157 | 0 | – |
| strict FR | 39,998 / 11,418 / 15,596 / 0 | -14 | -14 | 0 | – |
| **hostile** | 34,476 / -1,158 / 4,374 / 1 | **-2,538 (t -0.9)**; worst -134, CVaR 8,475, 1 losing | -1,162 (t -0.4); **worst -7,179**, CVaR 7,875 | +2,731 (t 2.0); **worst -9,850**, 2 losing | -2,669 (t -1.4); worst -6,770 |
| **everything** | 59,734 / -43,392 / -17,990 / 8 | +2,059 (t 0.3); worst -29,521, CVaR -2,793, **3 losing** | +6,763 (t 0.8); worst -15,141, CVaR 1,951, 4 losing | +1,807; worst -30,385, 9 losing | **-13,306 (t -2.0)**; worst unchanged |
| **hostile@a** | 3,635 / -7,272 / -2,826 / 5 | -136 (t -0.4); **worst -9,318**, CVaR -3,279 | -110; **worst -13,164, CVaR -5,729, 7 losing** | -411; worst -12,384, **9 losing** | -130 (t -1.2) |
| **everything@a** | -2,485 / -38,212 / -11,194 / 7 | -14; **worst -42,656** | -277 (t -0.7); worst -42,656, 8 losing | -255; 8 losing | -157 (t -1.0) |

Verdict under the agreed rule (no benign t <= -2; target scenarios t >= 2, or a clearly better worst / CVaR25
with no mean loss):

* **Benign: everything passes.** No benign t <= -2; the worst is -$157 (t -1.3). RACE lines are not zero,
  though: 6 in base and 44 in vol ×2, all from false gates (1 and 3 heats).
* **M1: fails, removed.** It passes "everything" (mean +$2.1k, worst -$43k → -$30k, CVaR -$18k → -$2.8k,
  losing 8 → 3). But it loses $2.5k in hostile, and in both anchored scenarios the mean is flat and the worst seed
  is worse (-$7.3k → -$9.3k, -$38k → -$43k). The gains come from the unanchored, displacement-edge scenarios:
  M1 gives up the best heats (hostile seed 212: $85.7k → $13.0k) and rescues the worst. That is path dependence
  on the artifact, not a mechanism that transfers.
* **M1 + M2: fails, removed.** Best on "everything" (worst -$43k → -$15k, CVaR -$18k → +$2k), but hostile's worst
  seed falls -$1.2k → -$7.2k. Anchored hostile: worst -$7.3k → -$13.2k, CVaR -$2.8k → -$5.7k, losing heats
  5 → 7. The prototype's anchored gain (+$1.7k / +$1.8k on 1-16 / 101-116) does not replicate (-$0.1k).
* **M2 alone: fails, removed.** Its falsification test was anchored hostile > 0 with t >= 2; it got -$0.4k,
  with losing heats 5 → 9.
* **M3 alone: fails, removed.** Its test was "everything" worst better by > $30k with a mean loss under $5k. It got
  worst unchanged, mean -$13.3k. (Its tail value in section 5 was measured on top of M1 + M2, which also fail.)

**Why M1 does not transfer, from the anchored losers.** In anchored mode the losing heats are made by
other desks' crowds (`othc`) and our own crowd before the bell. Seed 203 ("everything", anchored, -$38.2k):
* the bot accepts a 30k CRZY BUY at t37, before the crowd gate has a single observation;
* the next two CRZY BUY tenders (t38, t50) are declined, and their crowds take CRZY down $1.2 (`othc` -$31.7k);
* the gate opens at t48, so the race can only start after the ramp has begun. Selling into the ramp of a
  crowd whose move is half transient (residual 0.5 in the simulator) sells near the permanent level anyway,
  and pays the crossing.

So the reducible part of the anchored tail is RC4 (before the gate), not RC2. That is the section 5 M4
operational fix: set `crowd_prior_mean` from the practice rounds if they show a crowd.

**Losing heats, 201-232, current config (unchanged):** 0 in base, booking, strict, thin and 2× often;
2 in vol ×2, 3 in edge -10c, 1 in size ×2. In the target scenarios: 1 in hostile, 8 in everything, 5 in
hostile@a, 7 in everything@a.
