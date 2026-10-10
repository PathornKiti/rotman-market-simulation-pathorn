# Gap analysis: Liquidity Risk Case bot vs the real server (2026-10-11)

Brief: `document/Liquidity Risk Case - for selection 2027.pdf`. Bot: `liability`. Every gap below is
something the simulator assumed or the bot ignored that could differ on the real server. Each one is now
either **a stress scenario** (`python -m ritc stress`), **a bot change** (kept only if it passed the
usual rule: paired t ≥ 2, holdout confirmed, tail not worse), or **a measurement for the practice
heats** (`python -m ritc calibrate`, see [PRACTICE_PLAN.md](PRACTICE_PLAN.md)).

## Gaps and how each was filled

| # | Gap | Why it matters | Filled by | Result (fresh seeds 301-316 unless noted) |
|---|---|---|---|---|
| 1 | Resting fills free in the sim; brief only says "Commissions $0.02" | hold-to-the-bell lives on resting fills | stress `maker_fee=1` | −$4.3k vs base, 0 losing heats |
| 2 | Sim spread always 4c, so the bot steps inside and is first in queue | real spread may be 1-2c, then we queue behind others | stress `spread=0.02/0.01` + `--queue` (sim can now quote a 1-tick spread) | −$1.8k / −$3.1k, 0 losing |
| 3 | Resting fills may be rarer | same | stress `flow=0.3` | −$1.9k, worst $24.6k |
| 4 | Close at "last traded price" could land against holders | we end holding up to 125k shares | stress `close_slip=0.05` | −$1.4k, 0 losing |
| 5 | Other desks dump their blocks at the bell | same | stress `bell=0.20` ($ per 50k net tender flow, last 20 s) | −$8.1k, 0 losing |
| 6 | Bot speed / rate limits | lock-step tests ran 4 loops a second | stress `1 loop/s` | identical to base: the bot needs one decision per tick |
| 7 | **Bot answered tenders at once**, throwing away the 15-30 s window | a tender's price is fixed, the market is not | **bot: `decide_late_ticks = 3`** (kept) | **losing heats over 21 scenarios 13 → 1**; base +$25.3k (t 4.5) |
| 8 | Hostile sim priced tenders off the true price (fake edge) | inflated every hostile number | stress `anchor=1` (kept as the realistic hostile) | see table |
| 9 | Crowd timing: other desks may also answer late | then the crowd hits AFTER we commit | stress `crowd_at_expiry=1` | the one remaining losing region (below) |
| 10 | Auction reference price could be 0 on the real API | `min(bid, 0)` would bid $0, never win | bot: ignore a zero / absurd reference (test) | robustness only |
| 11 | `expires` unit on the real server unknown | a waiting bot could miss every tender | bot: answer at once if `expires` is missing / > 30 ticks away / unreadable; never wait > 30 ticks (test); log `seen at tick X, answering at tick Y` | verify in practice heat 1 |
| 12 | Stress harness only lived in a temp folder | can't re-run after calibrating | `python -m ritc stress` (22 scenarios, `--set`, `--only`, `--template`) | |
| 14 | Would a better vol model / HMM regimes help? | the bot's risk sizing uses GARCH | stress `vol_regime=2.5` + oracle vol (`RITC_ORACLE_VOL`) | perfect vol adds $0 (−$4.8k to +$0.3k): not built; `calibrate` vol-cluster Q + bot `VOL` log to check on real data |
| 15 | Where to rest the unwind (Cartea & Jaimungal fill model) | the sim only fills at the touch, so depth cannot be tested | `ritc record` stores time and sales; `calibrate` estimates P(trades reach d) and kappa per stock | measure on Sunday; build a depth rule only with that evidence |
| 16 | How much inventory risk to run | the bell window / hold budget are risk dials | `ritc stress --sweep` frontier with $-risk metrics | flat budget frontier; bell window 90 at the knee; profiles in the practice plan |
| 13 | Is there a crowd, and when does it hit? | decides the playbook | `calibrate` crowd event study, pooled over heats | validated: no crowd → all \|t\| ≤ 1; crowd at expiry → t +7.8 at expiry |

## Before → after (current config; fresh seeds 301-316, 16 heats per row)

| Scenario | Mean before → after | Worst before → after | Losing heats |
|---|---|---|---|
| base (brief) | 45,958 → **71,225** | 16,464 → 35,940 | 0 → 0 |
| volatility ×2 | 25,188 → **91,391** | −2,422 → 51,865 | 1 → 0 |
| worse tenders −10c | 13,145 → 36,636 | 1,350 → 3,016 | 0 → 0 |
| bigger tenders ×2 | 40,831 → 84,368 | 8,047 → 30,675 | 0 → 0 |
| hostile anchored (realistic) | 2,411 → **12,824** | −11,312 → 0 | **2 → 0** |
| everything anchored | −5,139 → **10,970** | −104,911 → −2,460 | **4 → 1** |
| real-world combined (gaps 1-5, slow bot) | 40,588 → 60,641 | 17,682 → 32,007 | 0 → 0 |
| real-world + hostile | 1,661 → **11,346** | −19,020 → 1,000 | **3 → 0** |
| hostile, old flawed pricing | 31,613 → 26,975 | 12,744 → 2,059 | 0 → 0 |
| **all 21 scenarios** | | | **13 → 1** |

The one row that got worse is the flawed-pricing hostile market: answering late forgoes the fake
"displacement edge" that only an instant answer could take (docs/RISK_REVIEW.md section 6).
Full table: `python -m ritc stress --seeds 16 --first-seed 301`.

## The remaining negative region: a crowd that unwinds at EXPIRY

`hostile crowd at expiry` (other desks get the same blocks and also answer late): mean $16.6k, but
5/16 losing heats, worst −$23.5k (seeds 1-16: 2/16, worst −$26.9k). Trace of the worst heat: the bot
holds 70k CRZY (thinnest stock) from two good tenders; at their expiry the crowd dumps the same
blocks and CRZY falls $0.60 in 10 ticks. At decision time nothing was visible, the hold budget kept it
passive, and racing out after the drop sells near the bottom (half of it reverts).

| Mitigation tried | Result | Verdict |
|---|---|---|
| Score the crowd from arrival to 10 ticks after expiry (`crowd_score_at`) | late crowd −$4.7k; vol ×2 −$6.9k (t −2.7: longer window, noise looks like a crowd) | removed |
| Crowd prior 0.05 / 0.10 (sd 0.03) | late crowd +$1.4-1.6k, worst unchanged; base −$0.6 to −$1.4k | no effect worth having |
| Fixed crowd charge 0.075 $/10k (noise 1.0, sd 0.01) | late crowd −$6.2k (seeds 1) / +$9.6k (301); **base −$41.5k (t −10)** if wrong | rejected |
| **Head start**: answer at once, unwind within 12 ticks, no hold | late crowd: worst −$26.9k → −$9.8k and −$23.5k → +$2.3k; CVaR25 −$9.0k → +$4.2k and −$14.5k → +$6.9k; losing 2 → 1 and 5 → 0 | **conditional playbook only**: −$34.2k a heat in a normal market and −$14.2k with an arrival crowd |

So the default stays, and the practice heats decide (PRACTICE_PLAN.md, "crowd check"):

| Pooled crowd event study (2+ heats) | Do |
|---|---|
| no cell with t ≥ 3 | defaults |
| crowd at **arrival** (`all|arrival` t ≥ 3, positive) | defaults: answering late already handles it (+$10-13k, 0 losing) |
| crowd at **expiry** only (`all|expiry` t ≥ 3, positive; arrival not) | head-start playbook: `--set strategy.decide_late_ticks=0 --set execution.hold_risk_budget=0 --set execution.close_hold_ticks=0 --set strategy.hold_valuation=false --set execution.unwind_horizon_ticks=12` |

## Retest of the final version (2026-10-11)

**Real time, over HTTP** (simulator on its own clock at 2 ticks/s, bot live, recorder watching; seed 777):
31 of 32 tenders answered, 29 of them 3-4 ticks before expiry, no errors, $39.3k. The one unanswered
tender arrived in the final 5 s, where the bot accepts nothing by design. The first run of this test found
a real gap: tenders whose window ran past the bell were left waiting until the bot could no longer accept
them (3 of 32 missed). Fixed with `decide_late_end_guard` (seeds 1-16 +$3.7k, t 4.1; 301-316 +$2.3k, t 2.6).

**All 23 scenarios on 32 brand-new seeds (401-432)**, `python -m ritc stress --seeds 32 --first-seed 401 --template`:

| Scenario | Bot mean | Bot worst | Bot losing | Template mean | Template losing |
|---|---|---|---|---|---|
| base (brief) | 65,086 | 25,189 | 0/32 | −4,852 | 20/32 |
| volatility ×2 / thin books / booking 3 s / strict front-running | 56-87k | 8.5-25.2k | 0/32 each | −22k to −5k | 18-27/32 |
| worse tenders −10c | 34,086 | 37 | 0/32 | −40,821 | 32/32 |
| bigger tenders ×2 | 71,408 | −607 | 1/32 | −22,902 | 25/32 |
| tenders twice as often | 119,713 | 65,313 | 0/32 | −546 | 18/32 |
| resting fills pay fee / spread 1-2c + queue / scarce fills / close 5c against / bell dump / slow bot | 60.9-65.7k | 14.1-30.0k | 0/32 each | −5k to 0 | 16-21/32 |
| real-world combined | 57,006 | 25,351 | 0/32 | −2,259 | 19/32 |
| vol regimes (hidden) | 75,554 | −229 | 1/32 | −5,154 | 18/32 |
| hostile market / hostile anchored | 34,475 / 16,788 | −8,090 / 0 | 1/32 / 0/32 | −81.5k / −78.1k | 31/32 |
| real-world + hostile | 16,879 | 0 | 0/32 | −75,383 | 31/32 |
| everything at once / everything anchored | 78,830 / 27,325 | −20,967 / −19,922 | 2/32 / 3/32 | −332k / −340k | 32/32 / 31/32 |
| **hostile crowd at expiry** | 20,263 | −39,781 | **9/32** | −6,914 | 20/32 |

Bot: 17 losing heats out of 736 (2.3%); 8 of 704 outside the known crowd-at-expiry weakness. Template:
loses on average in every scenario.

## Honest limits

- Every dollar figure comes from a simulator whose vol, depth, tender edges and crowd model are
  guesses; the mechanisms transfer, the magnitudes need the practice recordings.
- Answering late assumes the real tender price stays fixed until `expires` (the brief and the RTD
  doc's countdown suggest so). Verify in practice heat 1: the log line `seen at tick X, answering at
  tick Y` must precede every `TENDER ... ACCEPT/decline`, and no tender may expire unanswered.
- The bot now ends heats holding more inventory (up to 125k shares in base). The close-out stresses
  (gaps 4, 5) say that is still worth it, but it rests on the brief's "closed at the last traded price".
