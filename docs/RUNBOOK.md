# Competition-day runbook

## The night before

- [ ] `git pull`, then `pip install -e .` on the competition laptop (Windows, Python 3.11+).
- [ ] `python -m pytest -q`: everything green.
- [ ] Rehearse every case in the simulator: `python -m ritc sim <case>` +
      `python -m ritc run <case> --live -v`. Read the log in `logs/`.
- [ ] Print the five `config/*.toml` files and the case briefs.

## When the case brief is released (per case)

Copy these from the brief into `config/<case>.toml`:

| Field | Where in the brief |
|---|---|
| tickers / underlying / ETF components | "Securities" table |
| `fee` (per share/contract), `option_fee`, rebates | "Commissions" / "Trading fees" |
| `max_order` | "Maximum trade size" |
| `[risk.groups.*]` gross/net (name must match `/limits`) | "Trading limits" |
| derivatives: `expiry_ticks`, `ticks_per_year`, `multiplier`, `option_regex` | "Options" section |
| commodity: `futures` expiry ticks, `carry_per_tick`, `hedge_ratio` | "Storage costs", "Contract size" |
| `[run] max_drawdown` (kill switch) | Not in the brief: set to 2–3× the worst drawdown you saw in practice |
| etf: `components` weights, `fx_ticker`, `converter_cost` | "ETF composition", "Converter" |

## At the desk

1. Log into the RIT client. Click **API** on the bottom bar and copy the key into `.env`.
   Check that **API Orders** is green; if it is grey, you can only read.
2. `python -m ritc doctor`. Check that:
   - tickers match your config
   - `/limits` names match your `[risk.groups.*]` names
   - a news headline parses (derivatives and commodity)
3. In the practice round: `python -m ritc analyze`. If volatility clusters, set
   `vol_model = "garch"` in `config/equity.toml` (see docs/TIME_SERIES.md).
4. Before the bell: `python -m ritc run <case> -v` (dry run). The bot waits for ACTIVE.
5. Watch about 30 seconds of decisions. If they make sense, Ctrl-C and restart with `--live`.
6. In a second terminal: `python -m ritc monitor`.

## If something goes wrong

| Symptom | Action |
|---|---|
| Orders rejected "exceeds limit" | Lower `[risk]` numbers or `max_order`; check `/limits` in `doctor`. |
| HTTP 429 in the log | Set `RIT_MIN_INTERVAL=0.1` in `.env`, or raise `[run] interval`. |
| `OrdersDisabled` / 403 | API orders are off for this case. Trade by hand using `monitor`. |
| Bot doing something odd | **Ctrl-C** (cancels everything), switch to manual. |
| News not parsed | Add the phrasing to `pricing/news.py` (add a test!), or set the value in config and restart. |
| Position stuck at the end | `python -c "from ritc.core import RITClient as C; c=C(); c.cancel_all()"`, then flatten by hand. |

After each heat, read the **TCA report** at the end of the log. A large positive $/unit on
`aggressive` means you are paying too much spread; a low `fill%` on `passive` means
your passive orders aren't getting filled (raise `front_load` or shorten the schedule).

Every run writes a timestamped log to `logs/`. Keep them; they are your post-mortem.
