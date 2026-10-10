# Practice server plan: Liquidity Risk Case (opens Sunday 11 Oct 2026)

Brief: `document/Liquidity Risk Case - for selection 2027.pdf`. Bot: `liability`.

The brief leaves out exactly the numbers our settings depend on: how volatile "High" is, how deep
"Medium-low" liquidity is, how often tenders come and how good they are, how long the booking
delay is. The simulator guesses them. **The practice heats exist to measure them**, then to retune
the simulator and the bot on the real numbers before the graded heats. Trading P&L in practice is a
by-product.

Run every command on the **Windows PC running the RIT client**, from the repo folder.

## Before the server opens (Saturday)

- [ ] `pip install -e .` then `python -m pytest -q`: all green.
- [ ] `copy .env.example .env`, then paste `RIT_API_KEY` from the RIT client's API icon.
- [ ] Rehearse once offline: terminal 1 `python -m ritc sim liability`, terminal 2
      `python -m ritc record`, terminal 3 `python -m ritc run liability --live -v`. Then
      `python -m ritc calibrate logs\record-*.jsonl`.

## Every practice heat

Three terminals, started **before** the heat starts:

| Terminal | Command | Why |
|---|---|---|
| 1 | `python -m ritc record` | read-only recording (books, tenders, positions, **time and sales**), one snapshot every 0.5 s. Stops at the end of the case |
| 2 | the bot command for this heat (below) | |
| 3 | `python -m ritc monitor` | positions, limits, NLV at a glance |

After the heat, keep the files: `logs/record-*.jsonl` and `logs/liability-*.log`. Take
screenshots of **Trader Info** (Adjusted P&L, fines) and the **Transaction Log**.

## Heat by heat

| Heat | Bot command | What we learn |
|---|---|---|
| 0 (when you log in) | `python -m ritc doctor` | Check the connection. Look at **`--- limits ---`**: if the name is not `equity`, rename `[risk.groups.equity]` in `config/liability.toml` |
| 1: observe | `python -m ritc run liability -v` (DRY RUN, sends nothing) | Real vol, depth, spread; tender count, sizes, windows, edge; the **JSON fields of each tender type**: how do auction and winner-take-all show up (`is_fixed_bid`, `price`, `caption`)? Would the bot's ACCEPT/decline decisions have made money? |
| 2: cautious live | `python -m ritc run liability --live -v --set strategy.min_profit_per_share=0.05` | **Booking delay** (time from accept to position). **Fines must be 0**: check the Transaction Log. Does an auction we bid on **stay listed until it expires** (strict front-running reading) or vanish? |
| 3: live, our settings | `python -m ritc run liability --live -v` | Baseline P&L with the tuned config |
| between heats | `python -m ritc calibrate logs\record-<latest>.jsonl --json heatN.json` | Send me the printout and `heatN.json` |
| 4+: retuned | `python -m ritc run liability --live -v` (after we update the config) | Did the retune help? Same measurements again |

If there are fewer heats, do 1 → 2 → 3 in that order.

### The hold-to-the-bell check (heat 2 or 3): the biggest single setting depends on it

The bot now holds inventory in the last 90 s (and while its risk is under `hold_risk_budget`)
instead of crossing the spread, because the brief closes open positions at the last traded price
with no fine. Worth +$11k-15k a heat in the simulator. Confirm on the real server:

- [ ] At the end of a live heat the bot still holds a position: Trader Info and the Transaction Log
      show it closed at about the last price, and **no fine**.
- [ ] Resting orders actually fill (the log shows passive fills). If they barely fill, or the market
      looks manipulated or much more volatile than the recording says: next heat
      `--set execution.hold_risk_budget=20000`; to switch the hold off completely
      `--set execution.hold_risk_budget=0 --set execution.close_hold_ticks=0 --set strategy.hold_valuation=false`.
- [ ] Is the real spread at least 3 cents? If it is tighter, the bot rests behind other orders and gets fewer fills.
- [ ] A bid on an auction: does it return `success=false` at once, or only at expiry? Does the caption say "winner-take-all"?
- [ ] `/securities`: `trading_fee` and `limit_order_rebate` (is a resting fill charged?).

### Late-answer check (heat 1, dry run): the biggest setting depends on it

The bot answers each tender 3 ticks before it expires (`decide_late_ticks`), re-priced then
(+$25k a heat in the simulator, losing heats 13 → 1 over 21 stress scenarios; docs/POSTMORTEM.md).

- [ ] Every tender shows `TENDER <id> ... seen at tick X, expires E: answering 3 ticks before it` and later
      `TENDER <id> ... -> ACCEPT/decline`. **No tender may expire unanswered**, except one arriving in the
      final 5 seconds (the bot accepts nothing there by design).
- [ ] `calibrate` "windows (ticks)": if any window is longer than 30, run with
      `--set strategy.decide_late_max_window=<longest window + 5>` (otherwise those are answered at once).
- [ ] If anything looks wrong (tenders missed, answers after expiry, price changing during the window):
      `--set strategy.decide_late_ticks=0` (answer at once, the previous behaviour).

### Risk profiles (from the fine-tuning frontier, docs/RESEARCH.md)

| Profile | When | Add to the bot command | Simulator, seeds 401-432: base mean · crowd-at-expiry worst / losing · base peak position |
|---|---|---|---|
| Conservative | practice shows a crowd, manipulation, or resting orders that barely fill | `--set execution.close_hold_ticks=30 --set execution.hold_risk_budget=20000` | $63.7k · −$36.3k / 8 of 32 · 130k shares |
| **Default** | nothing unusual | (none) | $65.1k · −$39.8k / 9 of 32 · 158k shares |
| Aggressive | calm market, no crowd in 2+ heats, close-out confirmed free | `--set execution.close_hold_ticks=150` | $66.7k · −$39.8k / 10 of 32 · 158k shares |

### Crowd check (every live heat): sets the one setting that protects the early tenders

Grep the bot log for `CROWD` lines, e.g. `CROWD CRZY moved +0.350 against a 15k-share unwind -> 0.1538 $/10k (sd 0.0084, n=10)`.
They measure how far the price runs against a tender's unwind when other teams got the same block.
In the simulator, crowded markets lose almost entirely on tenders taken BEFORE the bot has seen a
crowd (the gate needs evidence first), and no code change fixed that on fresh seeds (docs/POSTMORTEM.md).

- [ ] Learned value clearly positive across heats → next heat
      `--set strategy.crowd_prior_mean=<value> --set strategy.crowd_prior_sd=<about half of it>`.
- [ ] Near 0 after 5+ tenders → leave the prior at 0 (a prior with no crowd costs about $5k a heat).
- [ ] **Pooled event study** (decide on 2+ heats, never one): `python -m ritc calibrate logs\record-*.jsonl`
      prints "POOLED crowd event study". A positive cell with t ≥ 3 means other desks unwind the same blocks:
  - `all|arrival` → keep the defaults (answering late already handles it).
  - `all|expiry` only → head-start playbook for the next heat:
    `--set strategy.decide_late_ticks=0 --set execution.hold_risk_budget=0 --set execution.close_hold_ticks=0 --set strategy.hold_valuation=false --set execution.unwind_horizon_ticks=12`
    (protects the tail in that world, but costs ~$34k a heat if there is no such crowd).

## Stop rules (Ctrl+C cancels every resting order; positions close at the last price, no fine)

- **Any speculation / front-running fine** in the Transaction Log → Ctrl+C, screenshot, send me the log.
- `OrdersDisabled` / 403 in the log → API orders are off: keep the bot in DRY RUN as a decision aid
  (it logs ACCEPT/decline and unwind sizes) and trade by hand.
- `rate limited` in the log → first slow the recorder (`python -m ritc record --interval 1`; it competes for
  the same API), then if needed add `RIT_MIN_INTERVAL=0.1` to `.env` and restart the bot.
- Position or net limit stuck near 100,000 → let it run (the bot will not accept tenders that
  break the limit), but tell me: it means tenders are bigger than the simulator assumes.

## What `calibrate` prints, and what we do with it

| Measured | Simulator setting it replaces | Bot settings that depend on it |
|---|---|---|
| `sigma/tick` per stock | `sigma` in `_setup_liability` | `risk_aversion`, `ac_risk_aversion` |
| depth within 5/10/25 c, by third, last 30 s | `lot`, CROC regimes, end-of-case boost | `refill_factor`, `unwind_horizon_ticks`, `participation` |
| tender gap, sizes, edge vs mid, fixed/auction mix | tender stream | `min_profit_per_share`, `competitive_margin` |
| windows; "left the list before expiry" | tender `expires`; strict front-running | `respect_windows` |
| booking delays | booking delay | `booking_ticks` |
| fill model: P(trades reach d beyond the mid per tick), kappa per stock (time and sales; our own ticks excluded) | the sim's fills happen only at the touch | where to rest the unwind: if P stays high 1-3 ticks beyond the touch (small kappa), resting deeper earns more per share; if it collapses (large kappa), rest at the touch as now. Build a depth rule only with this evidence |
| book imbalance → next mid move (r, t, sign hit) | nothing: the sim's book sizes are random, so it shows no signal (checked: all \|t\| < 2) | if \|t\| ≥ 3 on real heats, consistently across heats and stocks: an imbalance-timed unwind (cross when the next move is against us, rest when it is for us). Build and test only then |

Then: set the simulator to the measured numbers, re-run `python -m ritc stress` and `ritc tune` (~5 min per
setting), keep only changes that pass the usual rule (t ≥ 2, holdout confirmed), and use the new
config in the next heat.
