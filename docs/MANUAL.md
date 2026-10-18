# Manual: Liquidity Risk bot (macOS and Windows)

One bot, one case: tender offers. It prices each tender against the cost of unwinding the block, accepts what
clears fees, slippage and adverse drift, and unwinds in book-sized slices. Strategy docs:
[strategies/liquidity.md](strategies/liquidity.md), [RSI_STRATEGY.md](RSI_STRATEGY.md),
[RSI_TRADING_FLOW.md](RSI_TRADING_FLOW.md), [ARCHITECTURE.md](ARCHITECTURE.md).

The RIT client only runs on **Windows**, and its API (`localhost:9999`) only answers on that machine. So:

| | macOS | Windows |
|---|---|---|
| Develop, test, rehearse on the simulator, analyse logs | yes | yes |
| Trade on the real RIT server | no | **yes** |

Commands below use `ritc ...`; `python -m ritc ...` is identical.

## 1. Install (once)

Python 3.11 or newer.

**macOS (zsh)**
```bash
git clone https://github.com/PathornKiti/rotman-market-simulation-pathorn.git
cd rotman-market-simulation-pathorn
python3 -m venv .venv
source .venv/bin/activate            # do this in every new terminal
pip install -e ".[dev]"
cp .env.example .env
python -m pytest -q                  # must be all green
```

**Windows (PowerShell)**
```powershell
cd $HOME\Documents
git clone https://github.com/PathornKiti/rotman-market-simulation-pathorn.git
cd rotman-market-simulation-pathorn
python -m venv .venv
.venv\Scripts\activate               # do this in every new terminal
pip install -e ".[dev]"
copy .env.example .env
python -m pytest -q                  # must be all green
```
"running scripts is disabled": `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again.

Later, to update: `git pull`, and re-run `pip install -e ".[dev]"` if `pyproject.toml` changed.

## 2. `.env` (never commit it)

```
RIT_API_KEY=<key shown by the API icon in the RIT client>
RIT_HOST=localhost
RIT_PORT=9999
RIT_DRY_RUN=true
RIT_INTERVAL=0.25
RIT_MIN_INTERVAL=0.0
```
Save it as `.env`, not `.env.txt`. `RIT_DRY_RUN=true` is right: `--live` turns trading on per run.

## 3. Rehearse offline (macOS or Windows, no RIT client)

```bash
ritc sim liquidity                       # terminal 1: simulated market on localhost:9999
ritc run liquidity --live -v             # terminal 2: "live" against the simulator
ritc monitor                             # optional read-only dashboard
ritc stress                              # the bot across every adverse scenario
ritc tune liquidity --grid strategy.min_profit_per_share=0.03,0.05 --seeds 8   # A/B on the simulator
```
The simulator is not the real server: judge any change on real RIT heats first.

## 4. Before each real heat (Windows)

1. Open the RIT client and log in (server and trader ID/password from the organisers).
2. Bottom bar: the **API** icon shows port 9999 and your key; **API Orders** must be green.
3. Allow Python through Windows Firewall if asked.
4. `.venv\Scripts\activate`, then `git log --oneline -1` (must be the commit you expect) and
   `git status --short --untracked-files=no` (must list nothing).
5. `ritc doctor` prints case, trader, securities and tenders with no `FAILED`.
6. Start the bot **before** the heat opens; it waits for the case to go ACTIVE. Only ONE bot window per account.

## 5. Run profiles

Profiles are in `config/liquidity_profiles.toml`; the base settings are `config/liquidity.toml`.

| Profile | What it does | When |
|---|---|---|
| `A_default` | Adaptive front-load plus the ML sprint model; unwinds each block at once | Crowded or bot-heavy rounds (rsi_bot lost money against bots) |
| `rsi_bot` | Accepts every fixed tender priced better than the market, holds each block while the last 10 ticks run our way, unwinds when it turns; starts a hold only on a clear trend when the crowd alarm is on | Human-heavy, quieter rounds |
| `rsi_ml` | `rsi_bot` with the ML price model as the trend signal | Practice A/B against `rsi_bot` |

**Competition (graded heats, every heat, forever, market recorded):**
```
ritc run liquidity --live --forever --record --profile A_default
```
Swap `A_default` for `rsi_bot` when the counterparties are mostly humans. Check the first log line says
`PROFILE <name>`. Ctrl-C quits and cancels all resting orders. After a crash, restart the same command at once.

**Practice rotation:** `--profile rotate` (fewest heats so far), `--profile alternate:A_default,rsi_bot` (A, B, A, B...).

**Manual trading with the bot's logic (READ-ONLY, nothing is sent to RIT; you type the orders):**
```
ritc suggest --set strategy.accept_touch_edge=0.0 --set execution.trend_hold_ticks=10 --set execution.trend_hold_min_start_crowded=0.05
```
Do not run a bot window at the same time.

## 6. Logs and review

Each heat writes `logs/liquidity-<time>__<profile>.log` (read this: every tender with its edge and answer, blocks,
P&L every 30 s, and a HEAT REPORT at the end) and `logs/data/*.jsonl` (data for the tools).

```
ritc practice                     # compare profiles across the heats in logs/
ritc report logs/liquidity-*.log  # per-run gap report -> reports/*.html / .xlsx
ritc record                       # READ-ONLY: record a practice heat to logs/record-*.jsonl
ritc calibrate logs/record-*.jsonl
```
Switch rule used on the day: stay on one profile; switch only between heats, and only after two heats in a row
with the crowd alarm on and losing held blocks.

## 7. Knowledge kept in the repo

- `docs/results/ml_*`: the trained price/tender models (`models.json`), training and scoring code, reports.
  `ml_2026-10-16/models.json` is what `config/liquidity.toml` loads. Retrain: `docs/results/ml_2026-10-19/train.py`
  (needs scikit-learn).
- `docs/results/rsi_hold_2026-10-17`, `rsi_live_2026-10-18`: the RSI / trend-hold backtests and live results.
- `docs/PRACTICE_LOGS.md`, `docs/POSTMORTEM.md`, `docs/RESEARCH.md`: what was tried and what the evidence said.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `FAILED` in `ritc doctor` | RIT client not logged in, wrong key, or API Orders not green |
| `429` rate-limit lines | raise `RIT_INTERVAL` in `.env` |
| Nothing happens at the start | the bot waits for the case to be ACTIVE; start it before the heat |
| Bot misbehaving mid-heat | Ctrl-C (cancels all orders), then trade by hand with `ritc suggest` |
