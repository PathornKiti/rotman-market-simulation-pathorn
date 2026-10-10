# Runbook: install, set up a real case, competition day, after the heat

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
5. Run the bot on the same machine as the RIT client and keep antivirus HTTP inspection off localhost: slow
   loops cost money even when every tender is answered (-$9.5k a heat at 3 ticks per loop in the simulator).

| Symptom | Action |
|---|---|
| Orders rejected "exceeds limit" | Lower `[risk]` or `max_order`; check `/limits` in `doctor` |
| HTTP 429 in the log | `RIT_MIN_INTERVAL=0.1` in `.env`, or raise `[run] interval` |
| `OrdersDisabled` / 403 | API orders are off for this case: trade by hand with `monitor` |
| Bot doing something odd | **Ctrl-C** (cancels everything), switch to manual |
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
