# Official RITC rules, applied to our bots

Until the real case briefs are published, the most reliable guide is the **official RITC
case packages** from earlier years. They're mirrored, with Rotman's own support scripts and
the RIT REST API guide, in
[LiChiLin/Rotman-Trading-Competition-2024](https://github.com/LiChiLin/Rotman-Trading-Competition-2024)
(`2021 Rotman (Prev Case)/RITC2019_Case_Package.pdf`, `RITC2020_Case_Package.pdf`,
`2023 Rotman (Prev Case)/ritc-2023-case-package.pdf`). That repo has no licence, so we
**don't vendor anything from it**: this page records the rules, and the simulator and
configs follow them.

The cases have kept the same rules from 2019 to 2023 (often word for word). Expect the
same in the real case, but check every number against the new brief on the day.

**RITC 2026.** Rotman's lab publishes the newest package itself, in
[RotmanFRTL/RotmanFRTL.github.io](https://github.com/RotmanFRTL/RotmanFRTL.github.io)
(`RITC 2026 Case Package.pdf`, `RITC 2026-Closing Thoughts.pdf`, the RITCx briefs and base
scripts). It is copyrighted: as above, we record rules, never files. The 2026 cases are
Liquidity Risk (`liability`), Volatility (`derivatives`), GBE Electricity (API orders disabled),
**Merger Arbitrage** (new, no bot yet) and Algorithmic Market Making (`equity`), 20% each.
There is no ETF or BP commodity case in 2026; those live on in the RITCx events.

| Our bot | Official case | Years | API orders |
|---|---|---|---|
| `derivatives` | (MATLAB) Volatility Trading | 2019, 2020, 2023, 2024 | **enabled** |
| `etf` | (Citadel Securities) Algorithmic Trading: ETF + FX + tenders | 2019, 2020, 2023 | **enabled** (algo only, no manual trading) |
| `liability` | Liquidity Risk / Flow Traders ETF (tender offers) | 2019, 2020, 2023, 2024, 2026 | disabled 2019–2023, **enabled 2026** |
| `equity` | Algorithmic Market Making | 2024, 2026 | enabled (algo only, no manual trading) |
| `commodity` | BP Commodities (role-based crude + carbon credits) | 2019, 2020, 2024 | **disabled** |

---

## Scoring (all cases)

- For each heat, teams are **ranked** by P&L. The final case rank is the **average heat
  rank**, and each case carries the same weight.
- The packages put it directly: *"a team that places 8th, 5th, and 10th will have a higher
  final score than a team that places 1st, 10th, and 35th"*. Consistency beats a single
  big heat.
- This is why our tuner checks the worst seed and CVaR25 alongside the mean, and why
  `max_drawdown` is set to stop only catastrophes.

---

## Derivatives: Volatility Trading Case

| Rule | Official value | Ours |
|---|---|---|
| Heat | 600 s = 2 months (40 trading days), 2 periods × 300 ticks | `ticks_per_year = 3600` ✓ |
| Underlying | RTM, non-dividend ETF, starts at $50, **r = 0%** | ✓ |
| Options | European, 1- and 2-month, **10 strikes (45–54)**, calls and puts = 40 tickers `RTM1C45` … `RTM2P54` | sim now 45–54 (was 5 even strikes) |
| Quotes | Market makers always quote a **2-cent spread** for very large size ("no liquidity constraints"). Black-Scholes pricing with uninformed vol | sim spread 0.04 → **0.02**, deeper books |
| Fees | **$0.02/share RTM, $2.00/contract** | config `option_fee` 1.00 → **2.00**. Sim was charging $0.01 on both |
| Max order | 10,000 RTM, 100 contracts | ✓ |
| Limits | RTM 50,000 gross / 50,000 net. Options 2,500 gross / 1,000 net contracts | added `[risk.groups.etf]` for RTM. The sim now counts each group separately (it used to count every position against "options") |
| Vol regimes | 8 weeks of 75 ticks. Week start: *"The realized volatility of RTM for this week will be 20%"*. Mid-week (t = 38): *"The realized volatility of RTM for next week will be between 27-30%"* | **Parser bug fixed.** We only read "between X% and Y%", so the official range was silently ignored. The sim now uses this wording, and its range is about the vol actually drawn for next week (DEVLOG #4) |
| Delta limit | Announced by news, e.g. *"The delta limit for this heat is 5,000 and the penalty percentage is 0.5%"* (any integer > 1,000) | The parser now reads the penalty % too |
| Penalty | **Every second** with \|Δ\| > limit costs (\|Δ\| − limit) × p. It is deducted by the judges, not shown in RIT P&L | The sim accrues it and `ritc tune` now scores **NLV − penalty** |
| Close-out | RTM at the last price, options cash-settled at intrinsic | ✓ |

## ETF (`etf`): Algorithmic Trading Case

| Rule | Official value | Ours |
|---|---|---|
| Securities | BULL $10, BEAR $15 (CAD); **RITC $25 quoted in USD**; CAD, USD currencies | the sim now has USD and a USD-quoted RITC |
| Equilibrium | `P_RITC,USD × USD = P_BULL + P_BEAR` (USD is quoted as CAD per USD) | config `fx_ticker = "USD"`, `fx_mode = "divide"` |
| Fees | **$0.02/share** on market orders, **$0.01 rebate** on filled limit orders, all three | RITC fee 0.03 → 0.02. The sim now pays rebates |
| Max order | 10,000 shares (2019: 5,000 for stocks); 2,500,000 for currencies | ✓ |
| Limits | Gross/net across everything, with the **ETF counting ×2** | config weight RITC 1.0 → **2.0**. The sim reports the same |
| Close-out | Stocks at the last price; **ETF at fair value** (basket converted to CAD) | the sim closes RITC at NAV. Any arb still open converges for free at the bell |
| Converters | ETF creation/redemption, 10,000 units for $1,500 USD, **manual only, not via API** | `converter_cost = 0`: the bot never relies on them |
| Tenders | Private tender offers on RITC arrive in this case too | The sim sends them; the bot accepts when the basket hedge clears `tender_min_edge` (2026-10-07, +$3.7k) |
| FX | Hedge USD with the currency securities if desired | **Not** FX-neutral: the ETF closes at the CAD basket value, so in CAD the ETF leg is a CAD asset and the USD paid for it is a naked FX position (sim: ~$7k/heat of noise). Optional `fx_hedge_band` (off by default; CHANGES.md 2026-10-09) |

## Liability: Liquidity Risk Case

| Rule | Official value | Ours |
|---|---|---|
| Heat | 600 s = 1 month; 2–4 stocks per heat, $10–$75 | configure per heat |
| Commissions | $0.01–$0.04 per share, **differs by ticker and heat**; none on tenders | fill `fee` per heat |
| Limits | 250,000 gross / 150,000 net; max order 10,000 | ✓ |
| Tenders | Private (fixed price), competitive auction (any bid past a hidden reserve fills at your price), winner-take-all (best bid wins if past reserve). Window **15–30 s** | all three handled |
| **Adjusted P&L** | `P&L from tenders + min(0, P&L from speculation)`. Speculation = any trade not closing a tender position. **Front-running** = trading a ticker while a tender on it is pending (not yet accepted or declined) | `decline_explicitly` now **true** by default. Before, a rejected tender stayed pending for its whole window, so unwinding an earlier tender in the same ticker counted as front-running |
| Close-out | Last price; market makers add liquidity near the end | `urgent_ticks` |
| **API orders** | **Disabled** in 2019 and 2023 (the RTD/API data feed stays on); **enabled in 2026** | If disabled, run the bot **dry** as a decision aid: it logs `ACCEPT/decline` and unwind sizes, and a human places the trades |
| **2026: sub-heats** | 5 sub-heats of 600 s, each with **different stocks**: RITC/COMP, TRNT/MTRL, BLU/RED/GRN, WDY/BZZ/BNN, VNS/MRS/JPTR/STRN; fees $0.01–$0.04; tender windows 30 s, then 20 s | **Fixed 2026-10-09**: the bot only unwound the config's tickers, so a tender on any other stock was never covered. It now trades what `/securities` lists (fees and max order from the server row, each stock counts 1 share in the limits) |
| **2026: fines** (real time, in RIT P&L) | Flagged speculative / front-running shares: $1/share up to 5,000, $2 beyond. **$10/share** for any tender exposure still open at the end | unwind by `unwind_horizon_ticks`, `wind_down` crosses for the rest |
| 2026: booking delay | Observed by another team (Columbia, Sep 2026): an accepted tender appears in the position ~950 ms after the accept. Hedging before that was fined as speculation | we unwind from server positions, so we wait for the booking. The sim books instantly: untested |

## Commodity: BP Commodities Case

The official case is a **role-based** closed market: Producer, Refiner and 2 Traders per
team, trading crude (CL, 1,000 bbl contracts), RBOB/HO futures and carbon credits (RCA).
Max trade size is 5 contracts, positions are marked to market every 24 s, CL closes at a
fixed $30 and RCA at $50, and over-limit fines are $25,000 per contract. **API order
submission is disabled.** Our `commodity` bot (CL spot vs futures carry plus inventory-news
momentum) models a different, generic RIT commodity case. On the day it can serve only as
a signal display. No changes were made for this case.

## Equity: Algorithmic Market Making (RITC 2026)

| Rule | Official value | Ours |
|---|---|---|
| Heat | 12 heats of 300 s = 1 week; **each minute is a trading day**, news after each close (not shown to us) moves the stocks | sim: at each close, 80% chance of a common shock with a per-stock sensitivity plus own news (sizes are a guess) |
| Securities | SPNG, SMMR, ATMN, **WNTR**, all start at $25 CAD | sim and config now have WNTR |
| Fees | $0.02/share taker; passive rebate **SPNG 0.01, SMMR 0.02, ATMN 0.015, WNTR 0.025** | sim ✓ (the bot doesn't use rebates in its quotes yet) |
| **Aggregate limit** | \|SPNG\| + \|SMMR\| + \|ATMN\| + \|WNTR\|, announced at the start of the heat, assessed **at every market close: $10 per share over** (example: 15,000) | `close_every = 60`: quote only the reducing side from 5 ticks before a close, cross the spread for anything over 90% of the limit from 2 ticks before. Limit parsed from news (`parse_position_limit`, wording unpublished), 15,000 until then |
| Gross / net limits | Numbers not published; **$5/share** over them | `[risk.groups.equity]` 200k / 100k, tightened from `/limits` at start |
| Max order | 10,000 per stock | ✓ |
| Rules | All trades by algorithm; 2 minutes between heats to change it | — |

The official 2026 base script flattens everything from second 55 of each minute. The director's
closing note says the case "may be revamped" next year.

---

## Results under the official rules

See [CHANGES.md](CHANGES.md) for the backtests re-run on the updated simulator.
