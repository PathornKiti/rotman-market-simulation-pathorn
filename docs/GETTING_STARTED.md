# Getting started

## 1. What RIT is

The **Rotman Interactive Trader** is the market simulator used at RITC. Each team runs
the RIT *client* (Windows). The client connects to the competition server and also serves
a local REST API at `http://localhost:9999/v1`. These bots talk only to that local API.

## 2. Install

```bash
git clone <this repo> && cd <this repo>
python -m venv .venv && .venv\Scripts\activate      # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
cp .env.example .env
```

Requires Python 3.11+ (for `tomllib`). The only runtime dependency is `requests`.

## 3. Try it without RIT

```bash
python -m ritc sim equity              # terminal 1
python -m ritc run equity --live -v    # terminal 2
python -m ritc monitor                 # terminal 3, optional
```

The simulator prints NLV every 25 ticks. The bot logs every decision to the console and
to `logs/`.

## 4. Connect to the real client

1. Start the RIT client and log in.
2. Click the **API** icon on the bottom bar and copy the key into `.env` as `RIT_API_KEY=...`.
3. `python -m ritc doctor`. You should see the case, your trader and the securities as JSON.

## 5. Learn the code in this order

1. `src/ritc/core/book.py`: how a market order walks the book. Everything else depends on it.
2. `src/ritc/strategies/equity.py`: the simplest bot (`compute_quotes` is the whole idea).
3. `tests/test_strategies.py`: small, readable examples of what each bot decides.
4. The rest of `strategies/` and `docs/strategies/`.
