# Assumptions: Liquidity Risk Case bot (`liability`)

**As of 2026-10-10.** Bot: `src/ritc/strategies/liability.py` · Config: `config/liability.toml`

Every assumption the bot depends on, where it comes from, what relies on it, what happens if it is
wrong, and how we check it. The official brief leaves out most of the numbers the bot depends on, so
most of these are either read from other official documents, carried over from the 2026 rules, or
guessed in the simulator and measured in the practice heats ([PRACTICE_PLAN.md](PRACTICE_PLAN.md)).

## How to read this

**Sources**

| Tag | Document |
|---|---|
| Brief | `document/Liquidity Risk Case - for selection 2027.pdf` |
| RTD | `document/RIT - RTD_Documentation.pdf` (TENDERINFO format) |
| Help | `document/Liquidity help file.xlsx` (official Excel: tender fields, aggregated book depth) |
| Template | `Python package/Tender trade template.py` (official API example, tender JSON fields) |
| 2026 | RITC 2026 rules for the same case ([OFFICIAL_RULES.md](OFFICIAL_RULES.md)). A precedent; the 2027 brief may differ |

**Status**

- **Stated:** written explicitly in an official document.
- **Implied:** follows from the wording, not written explicitly.
- **Precedent:** true in the 2026 version of the case; not confirmed for 2027.
- **Unknown:** in no document; measured in practice or asked of the organisers.
- **Design:** our modelling choice, not a fact about the case.

**Impact if wrong**

- **Low:** the bot copes; at worst it misses an opportunity.
- **Medium:** a few $k less per heat, or small fines.
- **High:** a large loss.

## 1. Summary

- **One assumption has high impact: A15, the free close-out.** The bot deliberately ends heats
  holding inventory (up to ~125k shares in the base simulator) because the brief closes open positions
  at the last traded price. The 2026 version of this case fined **$10/share** for tender exposure still
  open at the end. The 2027 brief mentions no such fine, but this must be confirmed in a practice heat.
  If it can't be confirmed before a graded heat, switch the hold off:
  `--set execution.hold_risk_budget=0 --set execution.close_hold_ticks=0 --set strategy.hold_valuation=false --set strategy.min_ticks_to_unwind=15`
  (gives up ~$11-15k of simulated edge).
- **Two carry fine risk because the brief is ambiguous: A10 (front-running scope) and A8 (whether
  answering a tender ends its window).** The bot follows the 2026 definition (per stock, while the
  tender is pending). Worth asking the organisers (section 6).
- **Everything else is low or medium impact.** The bot prices every tender off the live order book and
  a live volatility estimate, and has guards for missing or odd tender fields. Across 23 stress
  scenarios that vary the unknowns (section 3), it had a positive mean in every scenario on 32 fresh
  seeds (401-432), and lost 17 of 736 heats, 9 of them in one known weak spot (B13).

## 2. Case rules and mechanics (A)

| ID | Assumption | Status (source) | Relies on it | Impact if wrong | Check / fallback |
|---|---|---|---|---|---|
| A1 | 1 tick = 1 s; one 420-tick period | Stated (Brief: 420 s; Help: "Total tick 420") | every horizon in ticks | Low: the bot reads `ticks_per_period` from `/case` | `doctor` |
| A2 | Stocks CRZY $10 / TAME $25 / CROC $20; $0.02 commission; max order 25k / 10k / 20k | Stated (Brief) | `[case]` in config | Low: the bot trades whatever `/securities` lists and takes fees and order caps from the server | `doctor` |
| A3 | Limits 250,000 gross / 100,000 net, enforced | Stated (Brief) | `[risk.groups.equity]`, risk room per tender | Low: the brief calls the limit `LIMIT-STOCK`; a server row whose name matches no configured group is ignored and the config's limits (same values) apply (`core/risk.py:78-90`) | `doctor` → `--- limits ---` |
| A4 | Orders through the API are enabled | Stated (Brief) | the whole bot | Medium: if disabled, run in dry-run mode as a decision aid and trade by hand | `OrdersDisabled` / 403 in the log |
| A5 | Prices are a random walk; returns normal, centred near zero | Stated (Brief) | no directional trading; risk ∝ σ√T (C5); holding costs nothing on average (C11); value of answering late (C17) | Medium: mean reversion → the bot is too cautious; trends → holding costs money | `ritc analyze` → `mean-rev` column |
| A6 | A tender's price is fixed for its whole window | Implied (Brief: "at the specified price"; RTD: a single price field) | `decide_late_ticks = 3` | Low: the bot re-reads the tender list each tick and prices at decision time | price in the `seen at tick X` log line = price in the `ACCEPT/decline` line |
| A7 | `expires` is a tick on the case clock; windows ~15-30 s, may vary by stock | Stated (RTD: "tick at which the offer will expire", example 49 → 79; Brief: example t = 0 to 30, "may vary by security"); 15-30 s is 2026 precedent | `decide_late_ticks`, `decide_late_max_window = 30` | Low: guards answer at once if `expires` is missing, unreadable or > 30 ticks away (`liability.py:417`) | `calibrate` "windows (ticks)"; raise `decide_late_max_window` if longer |
| A8 | Accepting or declining a tender ends its window, so the stock may be traded again at once | Implied (Brief: front-running is trading "before the tender decision is finalized"); Precedent (2026: "pending = not yet accepted or declined") | `decline_explicitly`, `respect_windows` (the stock is unfrozen once answered) | Medium: if the window runs to expiry whatever we answer, unwinding right after a decline could be flagged. No switch exists for this; it would need a small code change | Transaction Log fines = 0 (heat 2) |
| A9 | An accepted private tender reaches the position within ~1 s | Stated as "executed immediately" (Brief); Precedent (2026: ~950 ms observed; hedging before booking was fined) | `booking_ticks = 3`, `track_bookings` | Low if immediate (harmless); Medium if > 3 s (simulator: up to $2.8k fines before tracking existed) | `calibrate` "booking delays"; set `booking_ticks` = delay + 1 |
| A10 | Front-running is judged **per stock**: trading X is front-running only while a tender on X is undecided | Precedent (2026: "trading a ticker while a tender on it is pending"). Brief ambiguous: "any trades executed during this window" | `respect_windows` freezes only the tender's stock; other stocks keep unwinding | Medium-High: if trades in any stock count, unwinds during other stocks' windows are fined, and with a tender every ~12 s a window is open most of the heat | ask the organisers; Transaction Log fines = 0 |
| A11 | Speculation = a trade that opens a new position; reducing an accepted-tender position is never fined | Stated (Brief) | unwinds never pass zero (orders capped at shares left; `unwind_target`) | Low | Transaction Log |
| A12 | Competitive auction: any bid past the hidden reserve fills at our price; the award may only be final when the window closes | Fill rule Stated (Brief); timing Unknown ("automatically filled") | `freeze_rejected_bids` (stock frozen until expiry), booking wait = window | Low: if it fills at submission, freezing only costs some waiting | heat 2: when does `success` arrive; does the auction stay listed until expiry? |
| A13 | Winner-take-all: best bid past the reserve wins, decided at window end | Award rule Stated (Brief); timing Implied | same as A12 | Low | heat 2 |
| A14 | Tender JSON has `tender_id, period, tick, expires, caption, quantity, action, is_fixed_bid, price, ticker`; auctions have `is_fixed_bid = false` and maybe a 0 / empty price | Fields Stated (Template); how auctions look Unknown | `evaluate_tender`; a zero or absurd reference price is ignored (`liability.py:211`) | Low: worst case, auctions are missed (a missing `is_fixed_bid` is treated as a fixed tender) | heat 1 (dry run): tender JSON and captions |
| **A15** | **Open positions at the end are closed at the last traded price, with no fine** | Close-out Stated (Brief); "no fine" Implied (no fine listed); **Precedent against: 2026 fined $10/share for tender exposure open at the end** | `close_hold_ticks = 90`, `hold_risk_budget = 40000`, `hold_valuation`, `min_ticks_to_unwind = 5` | **High**: the bot ends heats holding up to ~125k shares (base simulator) | end of a live practice heat: Trader Info + Transaction Log. Fallback flags in section 1 |
| A16 | The last traded price at the bell is close to the mid | Stated (Brief: market makers increase liquidity near the end so the close can't easily be manipulated) | `hold_valuation` values late tenders at the mid | Low-Medium: stress "close 5c against" −$1.4k/heat, "other desks dump at the bell" −$8.1k/heat (seeds 301-316), both still positive | compare the closing price with the mid in the recording |
| A17 | The $0.02 commission is paid only by orders that take liquidity; resting fills are free | Unknown (Brief: "Commissions $0.02") | the economics of resting instead of crossing (C11-C13) | Low-Medium: stress `maker_fee=1` −$4.3k/heat (seeds 301-316), still positive | `/securities` `trading_fee`, `limit_order_rebate`; ask |
| A18 | No restrictive API rate limit | Unknown (the REST guide doesn't mention one) | polling every 0.1-0.25 s | Low: the client waits as a `429` asks; 1 loop/s gave identical results | `429` in the log → `RIT_MIN_INTERVAL=0.1` |
| A19 | Each team gets the same number of tenders; private tenders differ across teams in timing, price and size; auctions and winner-take-all go to every team at once | Stated (Brief) | crowd model (C9, C10, B13) | Medium: decides how crowded the market is | `CROWD` log lines; `calibrate` crowd event study |

## 3. Market numbers: simulator guesses, measured in practice (B)

The brief gives labels ("High", "Medium-low", "random intervals"), not numbers. The simulator
(`sim/server.py`, `_setup_liability` and `_tick_liability`) fills them in with guesses. Settings tuned
to these guesses are retuned once the practice heats measure the real values.

| ID | Quantity | Simulator guess | Brief says | Settings tuned to it | Result if different (stress, seeds 401-432 unless noted) | Measure with |
|---|---|---|---|---|---|---|
| B1 | Volatility per tick (std of the mid, $) | CRZY 0.020, TAME 0.025, CROC 0.040 | High / Medium / High | `risk_aversion`, `ac_risk_aversion` | vol ×2: positive, 0/32 losing | `calibrate` sigma/tick; bot `VOL` log line every 60 s |
| B2 | Volatility is constant (no clustering) | constant | random walk, normal returns | GARCH reduces to the sample volatility (C6) | hidden calm/turbulent regimes: $75.6k mean, 1/32 losing | `calibrate` vol-cluster Q (> 11.1 = clustering) |
| B3 | Book depth | 10 levels; base lot 1,200 / 3,000 / 2,000 shares; CROC depth ×0.4 / 1.0 / 1.6, redrawn every 40 s; up to 3× depth over the last 30 s | Medium-low / Medium-high / Varied; more liquidity near the end | `refill_factor = 2.0`, `unwind_horizon_ticks`, `participation` | thin books: positive, 0/32 losing | `calibrate` depth within 5/10/25¢, by third of the heat, last 30 s |
| B4 | Our price impact | mid moves 4e-6 / 1.5e-6 / 2.5e-6 $ per share we take (10k CRZY = 4¢) | — | `refill_factor` | (covered by thin books) | depth from `calibrate` |
| B5 | Spread; our resting order is first in the queue | 4¢ | — | resting at the touch (C15) | 1-2¢ spread + queue: −$1.8k / −$3.1k (seeds 301-316) | recording |
| B6 | Passive fills | flow reaches the touch with prob. 0.35 per tick per side; fills only at the touch | — | hold budget, bell hold | scarce fills (×0.3): −$1.9k (301-316) | `calibrate` fill model (P(trades reach d), kappa) |
| B7 | Tender frequency | on average every 12 s, none in the first 5 s (~34 per heat) | "random intervals" | `min_profit_per_share` | twice as often: $119.7k | `calibrate` tender gap |
| B8 | Tender size | 10k / 20k / 30k / 50k shares | "variations in … quantity" (RTD example: 58,000 TAME) | risk room vs the 100k net limit | double size: $71.4k, 1/32 losing | `calibrate` sizes |
| B9 | Tender edge vs mid | uniform −$0.10 to +$0.25 per share | "price influenced by the current market price" | `min_profit_per_share = 0.01`, `competitive_margin = 0.05` | 10¢ worse: $34.1k, 0/32 losing | `calibrate` edge vs mid |
| B10 | Tender mix | 75% private; 25% competitive (half auction, half winner-take-all) | three types, no proportions | — | — | `calibrate` fixed/auction mix |
| B11 | Auction reserve and rivals | reserve 0-15¢ beyond the mid; a winner-take-all rival beats the reserve 60% of the time, by 0-10¢ | "hidden baseline reserve price" | `competitive_margin` | margins 0.02-0.08: not significant in the normal market | our accepted / rejected bids in live heats |
| B12 | Booking delay | 1 tick | "executed immediately" | `booking_ticks = 3` | booking 3 s: 0/32 losing | `calibrate` booking delays |
| B13 | Crowd: other teams unwinding the same block | none in the normal simulator; the hostile simulator's crowd unwinds on arrival, or (stress) at expiry | auctions and winner-take-all go to everyone at once (A19) | `crowd_learn`, `crowd_gate`, `crowd_prior_mean = 0` | hostile (tenders priced off the visible price): $16.8k, 0/32 losing. **Crowd at expiry: $20.3k mean, 9/32 losing, worst −$39.8k (the known weak spot)** | `CROWD` log lines; `calibrate` pooled event study (t ≥ 3 over 2+ heats) |

## 4. Modelling assumptions in the bot (C)

Design choices, not facts about the case. Evidence is from the simulator (paired seeds; rule in section 5).

**Pricing a tender** (`evaluate_tender`, `liability.py:117`)

| ID | Assumption | Evidence / why | Weakness |
|---|---|---|---|
| C1 | The exit price is the average price of walking the order book with every level × `refill_factor = 2.0`: visible depth is about half of what can be sold over the unwind | `refill_factor` 1.5 / 3 / 4 tested; 3: +$3.4-5.1k normal but −$3.5-4.7k hostile, so 2.0 kept | one constant for all stocks and times; a live per-stock depth estimate was tried and removed (liquidity vacuums inflate it and the bot over-accepts) |
| C2 | Shares beyond the visible book are priced at the worst visible level − 5¢ | heuristic | untested against real depth |
| C3 | Commission is paid only on the part that must hit the book; the part that offsets an existing opposite position is valued at the touch | follows from the rules | — |
| C4 | Adverse drift: EWMA of mid changes (half-life 15 s), charged only when it runs against us, × expected unwind time (0.0005 s per share) | under a random walk (A5) this is ~0; a guard against short runs | — |
| C5 | Price risk while unwinding grows with σ√T. Charge: `risk_aversion` (0.3) × σ × √(unwind seconds) | `risk_aversion` 0: +$6.9k mean but worst heat $13.8k → $7.8k; 0.5: −$5.6k. Kept at 0.3 to protect the worst heat | depends on A5 |
| C6 | σ = per-stock GARCH(1,1) on per-second log mid returns (Gaussian likelihood, variance targeting, refit every 50 s); EWMA (λ = 0.94) until 60 returns; no estimate in the first 10 s ([ARCHITECTURE.md](ARCHITECTURE.md)) | given the **true** volatility instead, the bot gains −$4.8k to +$0.3k: forecasting skill is worth ~$0, what matters is the level (2× too high costs ~$8k) | a crowd or pump move looks like volatility; one-step σ × √T instead of the GARCH term forecast |
| C7 | Accept iff expected profit/share ≥ $0.01, the block fits the risk room, and ≥ 5 s are left | `min_profit` −0.01 to 0.03: not significant once holding to the bell | — |
| C8 | Competitive bid = the price that leaves $0.05/share; never worse for us than a sane reference price | 0.02-0.08 not significant normally; 0.02 / 0.03 −$3.3-3.8k hostile | reserve distribution unknown (B11) |

**Crowded tenders** ([ARCHITECTURE.md](ARCHITECTURE.md), [POSTMORTEM.md](POSTMORTEM.md))

| ID | Assumption | Evidence / why | Weakness |
|---|---|---|---|
| C9 | Crowd impact is linear in tender size ($ per 10k shares), the same for all three stocks, learned by a conjugate normal model from tenders we did **not** take, scored 10 s after arrival, and charged only once the estimate is > 2 sd above 0 | hostile +$11.4k (t 3.9), normal −$0.3k (t −1.0) | thin CRZY is under-charged (POSTMORTEM.md, risk review); the first tenders of a heat are unprotected until the gate opens (RC4) → set `crowd_prior_mean` from practice. Per-stock impact, crowd race and position cap all failed on fresh seeds |
| C10 | The crowd hits when a tender arrives | answering late handles an arrival crowd | a crowd at **expiry** is the known losing region (B13); the "head-start" playbook helps there but costs ~$34k a heat if there is no such crowd, so it's used only on practice evidence |

**Unwinding a block** (`unwind_block`, `core/algo.py`)

| ID | Assumption | Evidence / why | Weakness |
|---|---|---|---|
| C11 | Holding inventory costs nothing on average; crossing the spread always costs fee + spread + impact | follows from A5 + A15 + A17 | wrong if any of those three is wrong |
| C12 | Risk of holding to the bell = 1 sd = \|position\| × σ × √(seconds left). Only rest orders (never cross) while that is ≤ $40k per stock; cross the excess | +$4.3k (t 2.9), holdout +$5.0k (t 2.2); frontier flat from $20k to $80k | — |
| C13 | Never cross in the last 90 s; what's left closes at the last price | with `hold_valuation`: +$7.0k (t 3.8), holdout +$9.5k (t 4.6) | depends on A15 |
| C14 | Unwind speed follows Almgren-Chriss: linear temporary impact η = 1/(2ρ) from visible depth within 10¢, λ = 3e-7 per $, horizon 30 s (12 s once a crowd is detected) | $37.6k vs $34.1k for the old hand-tuned curve; ties a plain linear schedule here (impact dominates risk) | λ depends on the case's $ scale: recalibrate |
| C15 | Resting orders at the touch get filled by passive flow (iceberg showing ≤ 5,000 shares) | resting-then-crossing beat always-crossing on 8/8 seeds ($49.7k vs $24.1k, older version) | depends on B5, B6 |
| C16 | The three stocks move independently; risk is managed per stock | P&L attribution: losses are single-stock events; a net-limit shadow price was tried and removed | the brief says nothing about correlation |

**Timing and control**

| ID | Assumption | Evidence / why | Weakness |
|---|---|---|---|
| C17 | Answering 3 s before expiry is an option on the tender (accept the ones that moved our way) that costs only freezing the stock meanwhile | +$25.3k (t 4.5) on fresh seeds 301-316; losing heats over 21 stress scenarios 13 → 1; real-time HTTP test answered 31 of 32 tenders | needs A6, A7, and a round trip < 3 s |
| C18 | One decision per tick is enough | 1 loop/s gives the same results as 4 loops/s | — |
| C19 | No kill switch: in a random walk a drawdown predicts nothing, and stopping forfeits every later tender | hostile holdout +$18.2k (t 3.1), all stresses together +$69.7k (t 5.1), normal market unchanged | — |

## 5. How the evidence was produced (D)

| ID | Assumption | Note |
|---|---|---|
| D1 | The simulator reproduces the case's **mechanisms** (book walking, refill, crowds, fines, close-out, booking delay, winner-take-all) | its **magnitudes** are the guesses in section 3; dollar figures do not transfer, mechanisms do |
| D2 | A change is kept only if paired t ≥ 2 on design seeds, confirmed on fresh holdout seeds, and the worst heat is not worse | design seeds 1-16 / 101-116; fresh 201-232, 301-316, 401-432 |
| D3 | Changes must survive a hostile market too, judged with tenders priced off the **visible** price (`anchor=1`) | pricing off the true price gave a fake +$92k/heat "displacement edge" (POSTMORTEM.md, risk review) |
| D4 | The 23 stress scenarios span the plausible range of the unknowns in section 3 | positive mean in every scenario; the official tender template loses on average in every scenario |
| D5 | Practice heats measure the real values; then the simulator is reset to them and the settings retuned | `ritc record` → `ritc calibrate` → `ritc stress` / `ritc tune` |

## 6. Questions for the organisers

Each answer changes a setting. The bot already takes the safe reading of each.

1. **Close-out (A15):** is there any fine or charge on positions still open at the end, other than
   closing at the last traded price?
2. **Front-running (A10, A8):** during a tender's decision window, are trades in *other* securities
   flagged? Are trades that *reduce* an accepted-tender position in the *same* security flagged? Does
   accepting or declining end the window?
3. **Auctions (A12):** is a competitive auction fill decided when the bid is submitted or when the
   window closes? Can a bid be changed?
4. **Commission (A17):** is the $0.02 charged on passive limit-order fills? Is there a rebate?
5. **Scoring:** how are heats ranked (P&L per heat, total, or something else)? This decides whether
   to maximise the mean or protect the worst heat (several settings trade one for the other).

## 7. Open points for discussion

1. **Mean vs tail.** `risk_aversion = 0.3` and the bell hold trade mean P&L against the worst heat
   (C5, C12, C13). The right trade-off depends on how heats are scored (question 5).
2. **Holding to the bell.** Within the rules (A15), but the brief's objective 2 says large open
   positions "carry meaningful downside risk". Is a planned end-of-heat inventory a sound reading of
   the case's intent?
3. **Volatility model.** The brief says returns are normal. The value-of-information test says better
   volatility forecasting is worth ~$0, so GARCH serves as a per-stock volatility level that would
   also react if the real market clusters (C6, B2). Is a plain realised volatility equally defensible?
4. **Crowd impact model.** Linear in size and pooled across stocks (C9). Impact probably scales with
   1/depth (and may be concave in size); a per-stock version failed on fresh seeds. Is there a better
   structural model that needs fewer observations?
5. **Answering late as a game.** If every team answers late, crowds hit at expiry, which is exactly the
   bot's losing region (C10, B13). What is the equilibrium, and should the timing be randomised?
6. **Trusting a simulator built from our own guesses (D1-D5).** How far should settings tuned there
   be trusted before the practice recordings recalibrate it?

## 8. Practice-day mapping

| Heat | Checks |
|---|---|
| 0 (login) | A1-A4, A18 (`doctor`) |
| 1 (dry run) | A6, A7, A14; start recording for B1-B11 |
| 2 (cautious live, `min_profit_per_share=0.05`) | A8-A13, **A15**, A16, A17, B12 |
| 3+ | B13 / C9-C10 (crowd needs 2+ heats); retune from B1-B11 |

If time is short, check **A15, A10 and A6** first: together they cover the biggest simulated gains
and the biggest fine risk.
