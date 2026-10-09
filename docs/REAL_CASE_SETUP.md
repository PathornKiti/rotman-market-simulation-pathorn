# Preparing the bots for a real RIT case

The config files in `config/` ship with **simulator values** (tickers like `CRZY`, `RTM`,
`RITC`, `CL`). The bots do **not** discover assets from the server: they trade exactly the
tickers named in `config/<case>.toml`, in dry-run *and* live mode. Prices, positions,
news and tenders always come live from the server, but which tickers to look at, fees,
order sizes and case constants come from the config.

So once the admin has created the case on the server, fill in the config from two sources:

- **Server** → `python -m ritc doctor` (tickers, limit names, limit sizes)
- **Case brief** → the PDF/handout (fees, max trade size, expiries, carry, ETF weights)

The defaults already follow the official rules from past RITC case packages; see
[OFFICIAL_RULES.md](OFFICIAL_RULES.md) for what each case's brief usually says.

Only the `[case]` and `[risk]` sections (plus `[run] max_drawdown`) need editing. Leave
`[strategy]` and `[execution]` alone; they were tuned on the simulator (see
[PERFORMANCE.md](PERFORMANCE.md)).

> **Why it matters.** A ticker the config doesn't list is invisible to the bot: no quotes,
> no arb, and in the liability case a tender on it can be accepted but **never unwound**.
> A misspelled limit group name means the server's limit is silently ignored.

---

## Step 1: Connect and read the server

1. Open the RIT client and log in to the case.
2. Click **API** on the bottom bar and copy the key.
3. Create `.env`:
   ```bash
   cp .env.example .env
   ```
   Set `RIT_API_KEY=<your key>`. Keep `RIT_HOST=localhost`, `RIT_PORT=9999` unless the
   client runs on another machine (then set `RIT_HOST` to its IP and open port 9999 in the
   Windows firewall).
4. Stop any local simulator (`python -m ritc sim ...`), because it uses the same port.
5. Run:
   ```bash
   python -m ritc doctor > doctor.txt
   ```
   Keep `doctor.txt` open while editing. It contains:
   - `--- securities ---`: every `ticker`, its `max_trade_size`, `is_tradeable`
   - `--- limits ---`: every limit `name`, `gross_limit`, `net_limit`
   - `--- news ---`: the first headlines (to check the wording the parsers expect)

   Output is truncated to 1500 characters per section. For the full ticker list use
   `python -m ritc monitor --once`.

---

## Step 2: Fill in the config for your case

Legend: **[S]** = from the server (`doctor`), **[B]** = from the case brief.

### Liability trading: `config/liability.toml`

| Field | Source | Example (sim value) | Notes |
|---|---|---|---|
| `[case] tickers` | S | `["CRZY", "TAME"]` | **Every** ticker a tender can arrive on |
| `[case] fee` | B | `{ CRZY = 0.02, TAME = 0.02 }` | Taker commission per share, one per ticker |
| `[case] max_order` | S/B | `{ CRZY = 10000, ... }` | `max_trade_size` / "Maximum trade size" |
| `[risk.groups.<name>]` | S | `[risk.groups.equity]` | `<name>` must equal the limit `name` exactly |
| `gross`, `net` | S | `250000`, `150000` | `gross_limit`, `net_limit` |
| `weights` | B | `{ CRZY = 1.0, TAME = 1.0 }` | How much each share counts toward the limit |
| `[run] max_drawdown` | judgement | `15000` | See Step 3 |

If the brief says **"Order submission using the RIT API will be disabled"** (as in the
2019 and 2023 Liquidity Risk packages), run the bot as a dry run (`python -m ritc run liability -v`)
and use its `TENDER … -> ACCEPT/decline` and unwind log lines as a decision aid. Trade by hand,
and only to close tender positions: other trades count as speculation or front-running
under the Adjusted P&L ([OFFICIAL_RULES.md](OFFICIAL_RULES.md)).

### Equity trading (market making): `config/equity.toml`

| Field | Source | Example | Notes |
|---|---|---|---|
| `[case] tickers` | S | `["SPNG", "SMMR", "ATMN"]` | Tickers to quote |
| `[case] max_order` | S/B | `{ SPNG = 10000, ... }` | |
| `[risk.max_position]` | B | `SPNG = 25000` | One line per ticker; keep under the group limits |
| `[risk.groups.<name>]` | S | `gross = 200000`, `net = 100000` | Name must match `/limits` |
| `weights` | B | `{ SPNG = 1.0, ... }` | |
| `[run] max_drawdown` | judgement | `4000` | See Step 3 |

Optional: in the practice round run `python -m ritc analyze`. If it says
`clusters=YES`, set `vol_model = "garch"` (see [TIME_SERIES.md](TIME_SERIES.md)).

### ETF arbitrage: `config/etf.toml`

| Field | Source | Example | Notes |
|---|---|---|---|
| `[case] etf` | S | `"RITC"` | The ETF ticker |
| `[case] components` | B | `{ BULL = 1.0, BEAR = 1.0 }` | Shares of each component **per 1 ETF unit** |
| `[case] fee` | B | `{ RITC = 0.02, BULL = 0.02, BEAR = 0.02 }` | Every ticker, including the ETF |
| `[case] max_order` | S/B | `{ RITC = 10000, ... }` | Every ticker |
| `[case] fx_ticker` | S/B | `"USD"` | Official case: RITC in USD, stocks in CAD. `""` if they share a currency |
| `[case] fx_mode` | B | `"divide"` | USD quoted as CAD per USD → NAV_USD = NAV_CAD / USD. `"multiply"` for the inverse quote |
| `[case] converter_cost` | B | `0.0` | Amortised $/unit if the case has a creation/redemption converter |
| `[risk.groups.<name>]` | S | `gross = 300000` | Name, gross, net, weights for every ticker. Official: the ETF weighs **2.0** |
| `[run] max_drawdown` | — | `0` | **Keep at 0** (off on purpose, see [RISK.md](RISK.md)) |

### Derivatives (options volatility): `config/derivatives.toml`

| Field | Source | Example | Notes |
|---|---|---|---|
| `[case] underlying` | S | `"RTM"` | The stock ticker |
| `[case] option_regex` | S | `'^(?P<und>RTM)(?P<exp>\d)(?P<cp>[CP])(?P<strike>\d+(?:\.\d+)?)$'` | Must match the real option tickers (see below) |
| `[case] expiry_ticks` | B | `{ "1" = 300, "2" = 600 }` | Expiry code → absolute tick it expires |
| `[case] default_expiry_tick` | B | `600` | Used if a code isn't in `expiry_ticks` |
| `[case] ticks_per_year` | B | `3600` | e.g. 1 period = 300 ticks = 1 month → 3600 |
| `[case] rate` | B | `0.0` | Risk-free rate |
| `[case] multiplier` | B | `100` | Shares per contract |
| `[case] option_fee` | B | `2.00` | $ per contract (official: $2.00) |
| `[case] max_order` | S/B | `{ RTM = 10000 }` | Underlying |
| `[case] default_max_order` | S/B | `100` | Options, contracts per order |
| `[risk.groups.options]` | S | `gross = 2500`, `net = 1000` | Rename to match `/limits` |
| `pattern` | S | `'^RTM\d[CP]'` | Regex matching every option ticker |
| `[strategy] initial_vol` | B | `0.20` | Starting vol if the brief states one; news overrides it |
| `[strategy] delta_limit` | B | `7000` | Overwritten by news, but set the brief's value as a fallback |
| `[run] max_drawdown` | judgement | `80000` | Catastrophe-only stop, see Step 3 |

**Checking `option_regex`.** The named groups are required: `und` (underlying), `exp`
(expiry code), `cp` (`C` or `P`), `strike`. If the real tickers are e.g. `RTM50C1`, the regex
must be reordered. Test it before the case:

```bash
python -c "import re; r=re.compile(r'^(?P<und>RTM)(?P<exp>\d)(?P<cp>[CP])(?P<strike>\d+(?:\.\d+)?)$'); print(r.match('RTM1C50').groupdict())"
```

Replace the pattern and the sample ticker with the real ones. `None` / an error means no match,
and the bot would see no options at all.

### Commodity (spot vs futures): `config/commodity.toml`

| Field | Source | Example | Notes |
|---|---|---|---|
| `[case] spot` | S | `"CL"` | Spot ticker |
| `[case] futures` | S+B | `{ "CL-1F" = 300, "CL-2F" = 600 }` | Futures ticker → absolute expiry tick |
| `[case] carry_per_tick` | B | `0.002` | Storage + financing, $/unit/tick. Convert from the brief's units |
| `[case] fee` | B | `{ CL = 0.02, ... }` | Every ticker |
| `[case] hedge_ratio` | B | `{ "CL-1F" = 1.0, ... }` | Spot units per futures contract (contract size) |
| `[case] spot_shortable` | B | `true` | `false` if the brief forbids shorting spot |
| `[case] max_order` | S/B | `{ CL = 50, ... }` | Every ticker |
| `[risk.groups.<name>]` | S | `[risk.groups.crude]` | Name, gross, net, weights |
| `[run] max_drawdown` | judgement | `200` | See Step 3 |

**`carry_per_tick` example.** If the brief says storage costs $0.60 per barrel per period of
300 ticks, then `carry_per_tick = 0.60 / 300 = 0.002`. Add financing if the brief gives a rate.

---

## Step 3: Rescale the kill switch (`[run] max_drawdown`)

The defaults are calibrated to simulator P&L. The real case may make or lose 10× more or less.

- Set it to roughly **1.5–2× the worst normal drawdown** you saw in practice rounds.
- Never set it near a normal drawdown, or it will close winners at the bottom.
- **ETF: keep `0`.** Derivatives: keep it catastrophe-only (large).

Details: [RISK.md](RISK.md).

---

## Step 4: Check the news parsers (derivatives and commodity)

These two bots read headlines (`src/ritc/pricing/news.py`):

- **Derivatives** looks for phrasing like *"realized volatility … will be 25%"*,
  *"between 27-30%"* or *"between 20% and 30%"*, *"delta limit … 5,000 and the penalty percentage is 0.5%"*.
- **Commodity** looks for *inventory / stockpile / storage* with *build / draw* and an
  *expected / forecast / consensus* figure.

Compare with the headlines in `doctor.txt` and quick-test one:

```bash
python -c "from ritc.pricing.news import parse_vol_news as p; print(p('PASTE A REAL HEADLINE HERE'))"
python -c "from ritc.pricing.news import parse_inventory_news as p; print(p('PASTE A REAL HEADLINE HERE'))"
```

If a field comes back empty, either add the phrasing to `news.py` (with a test in
`tests/`) or set the value directly in config (`initial_vol`, `delta_limit`).

---

## Step 5: Verify before going live

1. **Dry run** (sends nothing):
   ```bash
   python -m ritc run <case> -v
   ```
2. In the log (`logs/`), check that:
   - every configured ticker shows a real bid/ask, with no empty books
   - no warnings about unknown tickers or failed requests
   - decisions look sensible (tenders evaluated, quotes near the market, arb edges plausible)
3. In a second terminal: `python -m ritc monitor`. Confirm the limits shown match your `[risk]`.
4. Confirm the **API Orders** icon in the RIT client is **green**.
5. Ctrl-C the dry run, then:
   ```bash
   python -m ritc run <case> --live
   ```

Ctrl-C at any time cancels all resting orders and stops the bot.

---

## Quick checklist

```
[ ] .env has RIT_API_KEY; simulator stopped; doctor runs without FAILED
[ ] tickers / etf / components / underlying / spot / futures = real tickers
[ ] fee and max_order set for EVERY ticker
[ ] [risk.groups.<name>] names match /limits exactly; gross/net/weights filled
[ ] equity: [risk.max_position] per ticker
[ ] derivatives: option_regex tested on a real ticker; expiry_ticks, ticks_per_year,
    multiplier, option_fee, rate; risk pattern matches options
[ ] commodity: futures expiry ticks, carry_per_tick, hedge_ratio, spot_shortable
[ ] etf: fx_ticker / fx_mode / converter_cost if the case has them
[ ] [run] max_drawdown rescaled (ETF stays 0)
[ ] news headline parsed (derivatives, commodity)
[ ] dry run clean -> API Orders green -> --live
```

See also: [RUNBOOK.md](RUNBOOK.md) for competition day and troubleshooting.
