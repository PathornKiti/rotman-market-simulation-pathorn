# Runbook: install, set up a real case, competition day, after the heat

**Step-by-step for the Windows machine (clone, venv, `.env`, run): [WINDOWS_SETUP.md](WINDOWS_SETUP.md).
Current state and next steps: [HANDOFF.md](HANDOFF.md).**

## 1. What RIT is

The **Rotman Interactive Trader** client (Windows) connects to the competition server and serves a local REST
API at `http://localhost:9999/v1`. The bots talk only to that API (key in the `X-API-Key` header).

## 2. Install

```bash
git clone <this repo> && cd <this repo>
python -m venv .venv && .venv\Scripts\activate      # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev,report]"                      # report = openpyxl, for the .xlsx reports
cp .env.example .env                                # RIT_API_KEY=...  (RIT_HOST / RIT_PORT if not local)
```

Python 3.11+. The only runtime dependency is `requests`.

Rehearse without RIT:

```bash
python -m ritc sim liability            # terminal 1: simulated market on :9999
python -m ritc run liability --live -v  # terminal 2: "live" against the simulator
python -m ritc monitor                  # terminal 3, optional read-only dashboard
```

Learn the code in this order: `core/book.py` (a market order walks the book), `strategies/equity.py`
(`compute_quotes`), `tests/test_strategies.py`, then the other strategies and [strategies/](strategies/).

## 3. Set up a real case

The configs ship with simulator values. The bots trade the tickers named in `config/<case>.toml` (liability
also picks up any stock `/securities` lists), so fill the config from two sources: the **server**
(`python -m ritc doctor > doctor.txt`: tickers, `max_trade_size`, limit names and sizes, first headlines) and
the **case brief** (fees, max trade size, expiries, carry, ETF weights). Past briefs: [OFFICIAL_RULES.md](OFFICIAL_RULES.md).
Edit only `[case]`, `[risk]` and `[run] max_drawdown`; `[strategy]` / `[execution]` were tuned on the simulator.

A ticker the config doesn't list is invisible (in liability a tender on it could be accepted and never
unwound), and a limit group whose name doesn't match `/limits` exactly is ignored.

| Case | Fill in (S = server, B = brief) |
|---|---|
| liability | `tickers` (S), `fee` per ticker (B), `max_order` (S/B), `[risk.groups.<name>]` gross / net / weights (S, name = `/limits` name) |
| equity | `tickers`, `max_order`, `[risk.max_position]` per ticker (B), groups (S); `vol_model = "garch"` only if `ritc analyze` shows clustering |
| etf | `etf`, `components` per ETF unit (B), `fee` / `max_order` for every ticker, `fx_ticker` / `fx_mode` / `converter_cost` (B), groups (ETF weighs 2.0 officially); `max_drawdown` stays 0 |
| derivatives | `underlying`, `option_regex` (test it on a real ticker: named groups `und exp cp strike`), `expiry_ticks`, `ticks_per_year`, `rate`, `multiplier`, `option_fee`, `default_max_order`, options group `pattern`, `initial_vol` / `delta_limit` fallbacks |
| commodity | `spot`, `futures` → expiry tick, `carry_per_tick` (e.g. $0.60 per 300 ticks = 0.002), `hedge_ratio`, `spot_shortable`, fees, groups |

If the brief says **API order submission is disabled**, run the bot as a dry run and use its
`TENDER ... -> ACCEPT/decline` lines as a decision aid; trade by hand only to close tender positions.

**Kill switch (`[run] max_drawdown`).** A catastrophe stop, not a P&L target: about 1.5-2x the worst normal
drawdown seen in practice, rescaled to the real case's P&L. Liability, ETF and equity run with it off
(a drawdown stop sells reverting drawdowns; [ARCHITECTURE.md](ARCHITECTURE.md#risk-controls)).

**News parsers** (derivatives, commodity): test a real headline before the heat:

```bash
python -c "from ritc.pricing.news import parse_vol_news as p; print(p('PASTE A REAL HEADLINE'))"
python -c "from ritc.pricing.news import parse_inventory_news as p; print(p('PASTE A REAL HEADLINE'))"
```

## 4. Competition day

The night before: `git pull`, `pip install -e .`, `python -m pytest -q` green, rehearse each case in the
simulator, print the configs and briefs. Liability: follow [PRACTICE_PLAN.md](PRACTICE_PLAN.md).

At the desk:

1. Log into the RIT client, copy the API key into `.env`, check **API Orders** is green (grey = read only).
2. `python -m ritc doctor`: tickers match the config, `/limits` names match `[risk.groups.*]`, a headline parses.
3. Start the bot **before the heat opens** (it waits for ACTIVE; a late start forfeits every tender before it):
   `python -m ritc run <case> -v` (dry run), check ~30 s of decisions, then `--live`.
4. Second terminal: `python -m ritc monitor`. Optional third: `python -m ritc record` (read-only recording).
5. **Manual trading (liability)**: `python -m ritc suggest` instead of `run`. Read-only (every non-GET call is
   refused): it runs the bot's own logic on YOUR positions and shows, per tender, ACCEPT / DECLINE / BID <price>
   with the $ edge and when to answer (WAIT until `decide_late_ticks` before expiry, then ANSWER NOW), and per
   stock the limit orders to type in (REST at the touch, or CROSS = marketable limit, cancel the leftover).
   Red `!` lines flag your resting orders that would be fined: through zero, wrong side, or on a stock whose
   tender is still open. Takes the same `--config` / `--set` as `run`; the terminal bell rings on a new tender.
   Start it before you have the key: it prints what is missing (empty key, key rejected, RIT not running) and
   retries every 2 s, re-reading `.env`, so it connects by itself once you paste the key and save.
   Rehearse with no key: `python -m ritc sim liability` + `python -m ritc suggest` in two terminals.
   **DMA instead of the RIT client** (no client needed, any machine that can reach the server): in `.env` set
   `RIT_URL=http://<server>:<port>/v1` and `RIT_AUTH=Basic <...>` (the header you were given). Same `/v1` paths
   (DMA swagger 1.0.5), so `doctor` / `suggest` / `run` all work over it.
6. Run the bot on the same machine as the RIT client and keep antivirus HTTP inspection off localhost: slow
   loops cost money even when every tender is answered (-$9.5k a heat at 3 ticks per loop in the simulator).

| Symptom | Action |
|---|---|
| Orders rejected "exceeds limit" | Lower `[risk]` or `max_order`; check `/limits` in `doctor` |
| HTTP 429 in the log | `RIT_MIN_INTERVAL=0.1` in `.env`, or raise `[run] interval` |
| `OrdersDisabled` / 403 | API orders are off for this case: trade by hand with `suggest` (liability) or `monitor` |
| Bot doing something odd | **Ctrl-C** (cancels everything), switch to manual with `python -m ritc suggest` |
| News not parsed | Add the phrasing to `pricing/news.py` with a test, or set the value in config |
| Position stuck at the end | `python -c "from ritc.core import RITClient as C; C().cancel_all()"`, flatten by hand |

## 5. After the heat

Every `ritc run` writes `logs/<strategy>-<date>-<time>.log` (text) and a **run journal** `.jsonl` next to it
(every tick's NLV, positions and quotes; every order, fill, tender, answer, error and slow loop). Then:

```bash
python -m ritc report                  # every logs/liability-*.log -> reports/
python -m ritc report --stress         # the current bot through every stress scenario (simulator only)
python -m ritc calibrate logs/record-*.jsonl   # if you recorded: measured vol / depth / tenders / crowd
```

`reports/liability-<date>-<time>_live.html` / `.xlsx` (RIT) or `_local` (simulator) has the P&L path, where it
came from, the struggle distribution and the gap checks; `reports/live-learnings.json` keeps every run's
numbers for the next review. What the reports contain: [POSTMORTEM.md](POSTMORTEM.md).

The TCA table at the end of each log: a large positive $/unit on `aggressive` = paying too much spread; a low
`fill%` on `passive` = resting orders not filling.

## 6. Practice-heat A/B testing (liability, real server)

**Why.** The simulator ranks ideas well where it models the mechanism (unwind speed, limits, fees) but it cannot
know the real auction reserve, whether other desks crowd a block, whether real prices trend, or whether the bell
is fined (RESEARCH.md, 2026-10-12). Those are settled on the practice server. The practice server works even with
no other teams logged in: its own bots keep the books liquid and still send private tenders and auctions. With no
other teams there is no real crowd, so any "crowd" the bot sees there is noise; keep that in mind for profile B.

**What to run, every practice heat.** One command, the same each time (Windows: WINDOWS_SETUP.md section 5):

```bash
git pull                                                     # once, before the first heat
python scripts/review_batch.py check                         # OK = config/liability.toml is the frozen bot (+ S, 10-15)
python -m ritc run liability --live -v --forever --record --profile alternate:A_default,P_previous   # A/B: A, P, A, P... this session
python -m ritc run liability --live -v --forever --record --profile alternate:A_default,M_ml_sprint  # ML sprint A/B (practice only)
python -m ritc run liability --live -v --forever --record --profile A_default   # the default every heat
python -m ritc run liability --live -v --forever --record --profile rotate      # least-tried profile in logs/
```

`--record` writes the whole market (books, tenders, time & sales) into each heat's journal, so the heats can be
replayed later (`docs/results/journal_replay.py`). Before a new A/B session, move the old `logs/` (including
`logs/data/`) into an archive folder: `rotate` counts every journal in `logs/`, so old `A_default` heats from
an earlier rotation would make it run the new profile several heats in a row instead of alternating.

`--forever` keeps the bot running across heats: when a heat ends (case STOPPED, or the clock jumps back to a
new heat) it closes that heat's log and journal, waits for the next heat to go ACTIVE and starts a fresh bot,
so you type the command once per session. Ctrl-C quits (and cancels resting orders) as before. Rehearse it
offline: `python -m ritc sim liability --heats 3 --gap 10` in one terminal, the command above in another.

Each heat's log and journal are named after what it changed, e.g.
`logs/liability-20261012-171840__C_auctions__crowd_robust=True+liquidity_model=auctions.log` (a plain run has
no suffix; `--set` overrides are listed the same way).

**Only ONE bot window per account.** With `--forever` the first window keeps trading every heat; starting a
second `ritc run --live` would put two bots on the same positions (both unwind, positions go through zero,
speculation fines; a -$45k heat on 2026-10-12 is the suspected case). A second live bot now refuses to start;
`ritc record`, `ritc practice`, `ritc monitor` and `ritc suggest` are read-only and fine in other windows.
`python -m ritc doctor` measures the RIT round trip on this machine: a loop is ~1 round trip idle and ~4-5
while unwinding, so "slow loops" during unwinds are expected above ~120 ms per round trip.

`--profile rotate` runs the profile from `config/liability_profiles.toml` with the fewest heats in `logs/` so
far, prints `PROFILE <name>` and records it in the journal and the file names. The profiles take turns:
A_default, P_previous, A_default, ... Start it before the heat opens, as always. To force one: `--profile A_default`. An
explicit `--set` still wins over the profile.

**Promoted (2026-10-15, user decision):** `S_staging` is now the default in `config/liability.toml` (`A_default` = S).
`P_previous` is the old frozen bot, kept so `--profile alternate:A_default,P_previous` can keep comparing them, above all in CROWDED heats. Single-change profiles stay in
`config/liability_profiles.toml` as comments with their evidence (uncomment one to re-test it alone).

| Profile | What it is | Evidence before the live test |
|---|---|---|
| `P_previous` | the previous frozen bot (b19e95c logic, config of commit b04253e) | baseline: 14 crowded evening heats mean $37k, never negative |
| `A_default` (= S) | `drift_horizon_ticks = 20` + `drift_zero_start = true` + `liquidity_model = "flow_guarded"` (flow pricing unless the crowd alarm is on) | real Rotman heats, paired replay (29 heats): +$6.9k/heat (t 3.6, 13 better / 1 worse, worst -$6.4k); live A/S holdout 18 heats +$3.3k..+$5.1k (t 1.2-1.9); 47 pooled +$5.4k (t 3.5). Open risk: the simulator's crowded markets reject it (-$7.5k/heat, worst heat -$240k) |

Windows launchers (WINDOWS_SETUP.md section 5): `scripts\staging.bat` runs every heat with the default
(`--profile A_default --forever --record`); `scripts\ab_default_staging.bat` alternates A_default / P_previous heat by heat (`--profile alternate:A_default,P_previous`). Either way the Mac
replays each recorded heat under both the previous bot and S (`docs/results/screening_2026-10-15/paired.py`), so staging-only heats
are still a paired comparison.

**Demotion rule for S (promoted 2026-10-15 by decision before the crowded test)** (judge the tenders it answers differently from P, not NLV alone):

| After | If | Then |
|---|---|---|
| each batch | send `logs/data/*.jsonl` | `review_batch.py review` + `docs/results/screening_2026-10-15/paired.py` (every heat replayed under A and S) |
| 4+ CROWDED heats of S (crowd alarm on, or 5+ traders) | S's extra tenders made money on balance there and no S block lost more than ~$30k | keep S |
| any crowded heat | an S block taken because of flow pricing loses more than ~$30k, or S trails A by > $20k in crowded heats | revert to `--profile P_previous` (or P + the drift cap, which passes the sim's rival gate) |

Don't run `ritc record` in a second window next to `--record`: the bot already records into its journal.

**Reading a heat's log** (liability): `TENDER ... ACCEPT (...) ~ $X expected` per tender answered;
`PNL t=210  NLV ... (+... in 30 s) | CROC -62,306 sh +27,564` every 30 s (RIT's realized + unrealized per stock);
`AUCTION #702 BUY CRZY 53k at 9.34: WON / lost` a few seconds after each auction closes; `BLOCK P&L CROC: +35,081
since tender #5 + #14 ... (expected +20,589)` when a block is flat again; at the end a `HEAT SUMMARY` table (every
tender: expected vs made, auctions won of bid) and `HEAT RESULT: NLV ... | per stock`.

**After each heat** (or a batch of heats), in the same folder:

```bash
python -m ritc practice                # every logs/liability-*.jsonl: one line per heat + by-profile summary
```

It prints, per heat: profile, final NLV, fixed tenders taken / offered and their estimated edge, auction wins /
bids, crowd alarms, median loop time and the **trend test** (variance ratio over 10 ticks per stock: ~1.0 =
random walk, > 1.2 = trends persist). Then every auction bid with how far it sat from the mid, won or lost: the
real reserve lies between the closest lost bid and the furthest won one. Also read **Total Fines** in the RIT
client after every heat and note it next to the heat: the journal cannot see fines.

**How to decide** (P&L swings $15-50k between heats, so judge on the steadier numbers first):

| After | If | Then |
|---|---|---|
| each batch | send `logs/data/*.jsonl` | `python scripts/review_batch.py review logs/data` (guards per heat) + per profile: the tenders it answered differently from A (replay) and its blocks' made vs expected |
| 5+ heats of a profile | its difference from A made money on balance (paired t >= 2, pooled with the replayed journals), no heat breaks a guard (worst block, one block per stock), and the simulator's rival-desk rows are not worse | make it the default: copy its line into `config/liability.toml` |
| 5+ heats of a profile | its difference lost on balance, or its worst block is the heat's big loss | drop it: comment it out in `config/liability_profiles.toml` |
| any heat | fines for speculation / front-running, or a crash | stop rotating, run `--profile A_default`, send the `.log` + `.jsonl` |

Heat P&L swings $15-50k, so do not judge B on NLV alone: the difference is in the few tenders where its answer
differs from A's. To make a winner the default for the competition, either run it by name
(`--profile B_drift_cap20`) or copy its lines into `config/liability.toml`. Upload the `.log` **and** `.jsonl` files of every practice heat: the
journals let the simulator be recalibrated to the real market, which is what makes its tests worth trusting.
