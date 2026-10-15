# Windows setup: clone, install, connect, trade (Liquidity Risk Case)

The RIT client only runs on Windows, and its API (`localhost:9999`) only answers on that machine, so the bot runs
there. All commands are **PowerShell** in the repo folder unless marked Mac.

## 0. Mac: publish the code first (once per change)

The Windows machine gets the code from GitHub. Work done on the Mac is invisible there until it is pushed.

```bash
cd ~/rotman-market-simulation-pathorn
git status                                   # what changed
git add -A
git commit -m "describe the change"
git push -u origin liability-fresh-unwind    # the working branch (main is behind)
```

## 1. Windows: get the code

First time:
```powershell
cd $HOME\Documents
git clone https://github.com/PathornKiti/rotman-market-simulation-pathorn.git
cd rotman-market-simulation-pathorn
git checkout main
```
Every later time (same folder):
```powershell
git checkout main
git pull
```
Check you have today's code: `git log --oneline -3` must show the latest Mac commit.

## 2. Windows: Python environment (first time, and after `git pull` if `pyproject.toml` changed)

Python 3.11 or newer (python.org; tick "Add python.exe to PATH" when installing). `python --version` to check.
```powershell
python -m venv .venv
.venv\Scripts\activate                       # prompt shows (.venv); do this in EVERY new terminal
pip install -e ".[dev,report]"
python -m pytest -q                          # must be all green before trading
```
"running scripts is disabled": run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again.

## 3. Windows: `.env` (first time; never committed)

`notepad .env` and paste (nothing else; no `RIT_URL`, no `RIT_AUTH`):
```
RIT_API_KEY=V0AGQ0GZ
RIT_HOST=localhost
RIT_PORT=9999
RIT_DRY_RUN=true
RIT_INTERVAL=0.25
RIT_MIN_INTERVAL=0.0
```
Save as `.env`, not `.env.txt`. If the RIT client's API icon shows a different key or port, use those.
`RIT_DRY_RUN=true` is right: `--live` turns trading on per run.

## 4. Before each heat

1. Open the RIT client, log in (server 161.200.68.42, port 10000, your trader ID and password).
2. Bottom bar: the **API** icon shows port 9999 and the key; **API Orders** must be green.
3. Allow Python through Windows Firewall if asked.
4. Check the connection:
   ```powershell
   .venv\Scripts\activate
   ritc doctor          # case, trader, securities, tenders print; no FAILED
   ```
5. Start the bot **before** the heat opens (it waits for the case to go ACTIVE).

## 5. Run

**Practice data collection (one window, every heat, default settings):**
```powershell
ritc run liability --live --forever --record
```
It waits for each heat, trades it with the default settings, records the market (books, tenders, time & sales),
then waits for the next heat. **One readable log per heat** in `logs\`:

```
logs\liability-20261014-0932.log        <- read this one
logs\data\liability-20261014-0932.jsonl  <- data for the analysis tools (never needs opening)
```

The `.log` has the story of the heat (every tender with its edge vs the mid and the answer, blocks, sprints, P&L
every 30 s) and ends with a **HEAT REPORT**: every tender taken or not (edge, the bot's value, expected and
made, late answers), every block (rested / crossed, mid move, made vs expected), the crowd, then the final NLV.
Order-by-order lines are only in the data file (`-v` puts them back in the log). A run that never saw a heat
(started after the last one, or Ctrl-C while waiting) deletes its own empty files. Don't run `ritc record` next
to it.

**Practice experiment (one window, heats take turns between the profiles, market recorded):**
```powershell
ritc run liability --live --forever --record --profile rotate
```
Same as above, but each heat runs the profile from `config\liability_profiles.toml` with the fewest heats in
`logs\` so far: `A_default` (the frozen bot), then `B_drift_cap20`, `C_flow`, ... `H_sprint003` (each one change,
listed in `config\liability_profiles.toml` and RUNBOOK.md section 6), then A again, and so on. The heat's first lines print `PROFILE <name>`, and the file names carry it:

```
logs\liability-20261015-140512__B_drift_cap20__drift_horizon_ticks=20.log
logs\data\liability-20261015-140512__B_drift_cap20__drift_horizon_ticks=20.jsonl
```

Before the session, after copying the old logs to the Mac: move the old `logs\` folder away (e.g. rename it
`logs_2026-10-14`). `rotate` counts every journal in `logs\`, so old `A_default` heats would make it run B
several heats in a row instead of alternating. Check the config first: `python scripts\review_batch.py check`
must print `OK` (the profile is added on top of it). To run one profile only: `--profile A_default`.

| What | Command |
|---|---|
| **Practice: every heat, default, market recorded** | `ritc run liability --live --forever --record` |
| **Practice experiment: profiles take turns, market recorded** | `ritc run liability --live --forever --record --profile rotate` |
| Practice, one profile only, market recorded | `ritc run liability --live --forever --record --profile A_default` |
| Bot, default (safe) settings, one heat | `ritc run liability --live -v` |
| Bot, **aggressive profile** (see PRACTICE_PLAN.md decision rules) | `ritc run liability --live -v --set strategy.liquidity_model=flow` |
| Dry run: decisions logged, nothing sent | `ritc run liability -v` |
| Manual trading with suggestions (read-only, you click) | `ritc suggest` |
| Read-only dashboard (2nd terminal) | `ritc monitor` |

Rules:
- **Ctrl-C** stops the bot and cancels its resting orders.
- **Don't click tenders or trade by hand while the bot runs `--live`.** To trade by hand: Ctrl-C the bot, then
  `ritc suggest`.
- Tenders are answered at once (`decide_late_ticks = 0`).

## 6. After each heat: keep the evidence

1. Screenshot RIT **Trader Info** (final NLV) and the **Transaction Log** (fines, commissions, close-out).
2. Copy `logs\` **including `logs\data\`** to the Mac (USB / drive / chat). The data files hold every order, the
   mid at every tender and, with `--record`, the whole market (~20 MB a heat).
3. On the Mac, put them in `logs/` (and `logs/data/`) and run `ritc report` (reports in `reports/`),
   `ritc practice` (scoreboard, one line per heat with its profile and a by-profile summary),
   `python scripts/review_batch.py review logs/data` (guards per heat) and
   `ritc calibrate logs/data/liability-*.jsonl` (market, pooled over heats).

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `waiting for the RIT API at http://localhost:9999/v1` | RIT client not open / not logged in, or wrong key or port in `.env` |
| `401` / unauthorized | key changed: copy it again from the API icon |
| `403` / OrdersDisabled | API orders are off for this case: use `ritc suggest` and trade by hand |
| HTTP 429 | `RIT_MIN_INTERVAL=0.1` in `.env` |
| `ritc` not found | the venv is not active: `.venv\Scripts\activate` |
| Tests fail after `git pull` | `pip install -e ".[dev,report]"` again |
