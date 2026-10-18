# Competition Runbook: Liability Risk case, Sun 2026-10-18, 13:00

**Bot:** `rsi_bot` (trend-hold tender strategy: `docs/RSI_STRATEGY.md`, flow: `docs/RSI_TRADING_FLOW.md`).
**Scoring:** average heat P&L across the graded heats. Goal: top 3.
**Fallback:** `A_default`, used only under the switch rule in §4.

> **CASE RULE (organisers):** The speculation fine will increase from $0.40 to $0.60 per share for speculation exceeding
> 5,000 shares. The fine for speculation of 5,000 shares or fewer remains unchanged at $0.20 per share.

| script (Windows, double-click) | what it does |
|---|---|
| `scripts\run_competition.bat` | Preflight checks (code version, config, RIT connection), then **rsi_bot**, every heat, forever, with the market recorded |
| `scripts\competition_status.bat` | Read-only scoreboard of today's heats plus the switch rule. Run it in a second window between heats |
| `scripts\run_competition_safe.bat` | The same preflight checks, then the **default bot**. Use only when the status script says SWITCH |

---

## 1. T−60 min: code (Mac, then Windows)

1. **Mac:** commit and push everything (`git status` must be clean afterwards).
2. **Windows**, in the repo folder:
   ```
   git pull
   .venv\Scripts\activate
   python -m pytest -q          (expect: 193 passed)
   ```
3. The last commit on Windows (`git log -1 --oneline`) must match the Mac.

## 2. T−20 min: RIT client

1. Log in to the RIT client with the team account. Open the Liability case.
2. Copy the **API key** into `.env` (`RIT_API_KEY=...`) and save.
3. Check that **API Orders is green** (grey = read-only: the bot cannot trade).
4. Close every other bot or `ritc` window. Run **only one bot per account**.
5. Turn off sleep and screensaver, and plug in the laptop. Keep the RIT client and the bot on the same machine.

## 3. T−5 min: start the bot (before the first heat opens)

Double-click `scripts\run_competition.bat` and read its four steps:

| step | must show | if not |
|---|---|---|
| 1/4 code version | the Mac's last commit, and **nothing** listed under it | `git stash` or `git checkout -- .`, then `git pull`, then restart |
| 2/4 config check | `OK: config/liability.toml = the frozen bot ...` | do not trade; `git pull` again |
| 3/4 RIT connection | `median ... ms` lines, **no `FAILED`** | log in to RIT, fix the API key, make API Orders green, restart the script |
| 4/4 run | the first log lines say `PROFILE rsi_bot {...}` and `strategy=liability dry_run=False`. Before the heat opens, the bot waits silently for the case to go ACTIVE | Ctrl-C, check the profile name, restart. `dry_run=True` = `--live` is missing |

Leave this window open for the whole competition. `--forever` picks up each new heat by itself (`=== heat N finished - waiting for the next heat to start ===`) and writes one `.log` plus one `.jsonl` per heat.

## 4. During the heats

**Do nothing by hand.** Don't click trade or tender buttons in RIT while the bot runs: manual trades can go through zero and be fined as speculation.

Normal log lines:

| line | meaning |
|---|---|
| `TENDER ... -> ACCEPT (profit/share ...)` | accepted by the valuation |
| `TOUCH ACCEPT ...` | accepted because the price beats the market |
| `TREND HOLD X: holding our ±N` | riding a trend, so no orders on X (normal: it can last 100+ ticks) |
| `TREND HOLD X off: ... held N ticks, mid +x our way since` | the trend turned and the block is being unwound |
| `BLOCK DONE ... made ...` | block closed, with its P&L |
| `... CROWDED` | crowd alarm on: holds start only on a $0.05 trend |
| `slow loops ...` | occasional ones are harmless |
| `HEAT RESULT: NLV ...` | the heat's final result |

A block still held at the bell is **expected**. The case closes it at the last price with no fine.

### Between heats: the switch rule (fixed in advance; do not improvise)
Double-click `scripts\competition_status.bat` in a **second** window. It reads the logs only and never talks to RIT.

- **STAY** on rsi_bot unless the script prints `=> SWITCH`.
- `SWITCH` = the **last two** rsi_bot heats **both** had the crowd alarm on **and** lost money on held blocks.
- To switch, between heats only: Ctrl-C in the bot window, wait for `shutting down: cancelling all resting orders`, then start `scripts\run_competition_safe.bat`.
- One bad heat is **not** a reason to switch. The strategy's P&L is skewed: a few long trends make most of the profit.
- If the script prints `!! RIT shows fines`, read that heat's log before the next heat.

## 5. Troubleshooting

| symptom | action |
|---|---|
| `waiting for the RIT API ... Must be logged in` | log in to the RIT client. The bot retries every 2 s by itself |
| `UNAUTHORIZED ... Simulation must be running` at a heat's end | normal: the heat ended while orders were being cancelled |
| HTTP 429 in the log | add `RIT_MIN_INTERVAL=0.1` to `.env`, then restart the bot between heats |
| Orders rejected "exceeds limit" | check `/limits` in `logs\doctor-competition.txt`; never above gross 250k / net 100k |
| The bot window closed or crashed mid-heat | restart `scripts\run_competition.bat` at once. It picks up the current positions and unwinds them |
| The bot is doing something clearly wrong | **Ctrl-C** (cancels all orders). Switch to manual trading with `scripts\suggest_rsi.bat` (read-only rsi_bot advice) and close positions by hand **only toward zero** |
| A position is stuck at the end | leave it: the case closes it at the last price, with no fine |

## 6. After the competition

1. Copy every `logs\*.log` and `logs\data\*.jsonl` to the Mac (`logs/` and `logs/data/`).
2. Mac: `python scripts/competition_status.py logs --since 20261018-120000` for the graded-heat scoreboard, and `python -m ritc report` for the HTML/XLSX reports.
3. Commit the logs summary (not the logs themselves: `logs/` is ignored by git).
