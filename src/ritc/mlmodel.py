"""
ML PRICE MODEL (2026-10-15): predict the mid's move over the next 10 ticks from the last 20 ticks of mids, the spread,
the visible book (others' orders, top 10) and traded volume. Trained offline (docs/results/ml_2026-10-15/train.py) and
exported as plain JSON, so the bot needs no ML library. The SAME feature builder is used for training and live, so the
model sees identical inputs in both. Used by the liquidity bot's SPRINT rule when `strategy.sprint_model` is set.
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

from .core.book import OrderBook

PRICE_COLS = ["r1", "r5", "r10", "r20", "vol20", "spread", "dimb", "dtot", "flow10", "tick", "is_CRZY", "is_TAME"]
ROOT = Path(__file__).resolve().parents[2]          # the repo


def book_depth(book: OrderBook, n: int = 10) -> tuple[float, float]:
    """Shares in the first `n` resting orders on each side (others' orders only: OrderBook drops our own)."""
    return float(sum(lv.quantity for lv in book.bids[:n])), float(sum(lv.quantity for lv in book.asks[:n]))


def price_features(mids: list[float], vols: list[float], book: OrderBook, spread: float, tick: int,
                   ticker: str) -> dict | None:
    """`mids` / `vols`: one mid / cumulative traded volume per tick, oldest first, ending NOW. None until 21 mids."""
    if len(mids) < 21:
        return None
    m = mids[-21:]
    d = [b - a for a, b in zip(m, m[1:])]
    bid_d, ask_d = book_depth(book)
    tot = (bid_d + ask_d) or 1.0
    return dict(r1=m[-1] - m[-2], r5=m[-1] - m[-6], r10=m[-1] - m[-11], r20=m[-1] - m[0], vol20=st.pstdev(d),
                spread=spread, dimb=(bid_d - ask_d) / tot, dtot=tot,
                flow10=(vols[-1] - vols[-11]) if len(vols) >= 11 else 0.0, tick=float(tick),
                is_CRZY=float(ticker == "CRZY"), is_TAME=float(ticker == "TAME"), bid_d=bid_d, ask_d=ask_d)


def predict_linear(m: dict, x: list[float]) -> float:
    return m["intercept"] + sum(c * (v - mu) / sc for c, v, mu, sc in zip(m["coef"], x, m["mean"], m["scale"]))


def predict_trees(m: dict, x: list[float]) -> float:
    out = m["baseline"]
    for nodes in m["trees"]:
        i = 0
        while not nodes[i]["leaf"]:
            n = nodes[i]
            i = n["left"] if x[n["f"]] <= n["thr"] else n["right"]
        out += nodes[i]["value"]
    return out


def predict(m: dict, row: dict) -> float:
    x = [float(row[c]) for c in m["cols"]]
    return predict_trees(m, x) if m["kind"] == "trees" else predict_linear(m, x)


def load(path: str, key: str = "price_trees") -> dict:
    """One model from an exported models.json; a relative path is taken from the working directory, then the repo."""
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = ROOT / p
    return json.loads(p.read_text())["models"][key]
