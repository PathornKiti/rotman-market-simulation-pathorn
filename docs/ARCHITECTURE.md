# Architecture

```
            config/<case>.toml           .env (API key, host, dry-run)
                   │                            │
                   ▼                            ▼
 python -m ritc run <case> ──► cli.build() ──► RITClient ──HTTP──► RIT Client :9999/v1
                                   │                                (or ritc.sim on :9999)
                                   ├─► Executor  (dry-run, protective limits, slicing, IOC sweep)
                                   ├─► RiskManager (gross/net limit groups)
                                   └─► Strategy  (one of five) ◄── Runner loop
```

## The loop (`core/bot.py`)

```
every `interval` seconds:
    executor.sweep()               # cancel unfilled remainders of last loop's aggressive orders
    snap = case + securities       # one round trip; books fetched lazily per ticker
    if not ACTIVE: wait            # safe to start before the bell
    first time: strategy.on_start(snap)
    new period: strategy.on_new_period(snap)
    last N ticks: strategy.wind_down(snap)   else: strategy.step(snap)
on Ctrl-C / crash / STOPPED:
    executor.cancel_all(); strategy.on_stop()
```

## Layers

| Layer | Knows about | Does not know about |
|---|---|---|
| `core.client` | HTTP, RIT endpoints | strategies |
| `core.book` | order-book maths | the network |
| `core.execution` | how to send orders safely | why they are being sent |
| `core.risk` | limits and positions | prices |
| `pricing.*` | maths and parsing | the API |
| `strategies.*` (pure functions) | decision logic | the network |
| `strategies.*` (Strategy classes) | wiring it together | — |

## Adding a sixth strategy

1. Create `src/ritc/strategies/mycase.py` with pure decision functions and a
   `class MyStrategy(Strategy)` that implements `step(snap)`.
2. Register it in `strategies/__init__.py` (`REGISTRY["mycase"] = MyStrategy`).
3. Add `config/mycase.toml` with `[case]`, `[risk]`, `[strategy]` and `[run]` sections.
4. Add unit tests for the pure functions. Optionally add a `_setup_mycase` / `_tick_mycase`
   pair to `sim/server.py` so the end-to-end test covers it.

## RIT API notes

- The REST API is served by the **RIT client on your own machine**
  (`http://localhost:9999/v1`), authenticated with the `X-API-Key` header.
- `/securities/book` returns **singular** keys (`bid`, `ask`). `OrderBook.from_api` accepts both.
- RIT has no IOC order type. `Executor.sweep()` emulates it.
- A 429 response tells you how long to wait (`wait` in the body). The client honours it
  (capped at 2s). Raise `RIT_MIN_INTERVAL` if you hit it often.
- Tenders: `POST /tenders/{id}` accepts (with `price=` for non-fixed bids),
  `DELETE /tenders/{id}` declines.
- Leases (storage, refineries in commodity cases): `GET/POST /leases`,
  `POST /leases/{id}` to use one. Wrappers are in `RITClient`.
