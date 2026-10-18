import json
import math

from ritc import mlmodel
from ritc.core.book import OrderBook


def test_price_features_match_their_definitions_and_skip_our_own_orders():
    book = OrderBook.from_api({"bids": [{"price": 9.99, "quantity": 5000, "quantity_filled": 1000, "trader_id": "ANON"},
                                        {"price": 9.98, "quantity": 9000, "trader_id": "me"}],
                               "asks": [{"price": 10.01, "quantity": 2000, "trader_id": "ANON"}]}, "CRZY", "me")
    assert mlmodel.book_depth(book) == (4000.0, 2000.0)                    # our 9k resting bid is not the market's
    mids = [10 + 0.01 * i for i in range(21)]
    assert mlmodel.price_features(mids[1:], [], book, 0.02, 30, "CRZY") is None    # 20 mids: not enough history
    f = mlmodel.price_features(mids, [100.0 * i for i in range(11)], book, 0.02, 30, "CRZY")
    assert math.isclose(f["r1"], 0.01) and math.isclose(f["r10"], 0.10) and math.isclose(f["r20"], 0.20)
    assert f["vol20"] < 1e-9 and f["flow10"] == 1000.0 and f["is_CRZY"] == 1.0 and f["is_TAME"] == 0.0
    assert math.isclose(f["dimb"], 2000 / 6000) and f["dtot"] == 6000.0


def test_tree_predictor_and_the_exported_model():
    tree = [{"leaf": False, "f": 0, "thr": 0.5, "left": 1, "right": 2, "value": 0.0},
            {"leaf": True, "value": -0.1, "f": 0, "thr": 0, "left": 0, "right": 0},
            {"leaf": True, "value": 0.3, "f": 0, "thr": 0, "left": 0, "right": 0}]
    m = {"kind": "trees", "baseline": 0.01, "trees": [tree], "cols": ["a"]}
    assert math.isclose(mlmodel.predict(m, {"a": 0.2}), -0.09) and math.isclose(mlmodel.predict(m, {"a": 0.9}), 0.31)
    real = mlmodel.load("docs/results/ml_2026-10-15/models.json")
    assert real["cols"] == mlmodel.PRICE_COLS and len(real["trees"]) > 50
    x = dict.fromkeys(mlmodel.PRICE_COLS, 0.0) | {"tick": 100.0, "dtot": 50000.0, "spread": 0.03}
    assert abs(mlmodel.predict(real, x)) < 0.5


def test_ml_sprint_model_drives_the_expected_move_against_the_block(tmp_path):
    from ritc.core.bot import Context, Snapshot
    from ritc.core.execution import Executor
    from ritc.core.risk import RiskManager
    from ritc.practice import pick_profile
    from ritc.strategies.liquidity import LiquidityStrategy

    leaf = [{"leaf": True, "value": 0.2, "f": 0, "thr": 0, "left": 0, "right": 0}]
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": {"price_trees": {"kind": "trees", "baseline": 0.0, "trees": [leaf],
                                                           "cols": mlmodel.PRICE_COLS}}}))
    cfg = {"case": {"tickers": ["X"]}, "strategy": {"sprint_model": str(path)}}
    s = LiquidityStrategy(Context(None, Executor(None), RiskManager(), cfg))
    s.sync_tickers(Snapshot({"tick": 1}, {"X": {"bid": 24.99, "ask": 25.01}}))
    for tick in range(1, 26):
        snap = Snapshot({"tick": tick, "period": 1, "ticks_per_period": 420},
                        {"X": {"bid": 24.99, "ask": 25.01, "volume": 1000 * tick}})
        snap._books["X"] = OrderBook("X")
        s._update_drift(snap)
        if tick == 20:
            assert "X" not in s.ml_pred                  # 20 ticks of history: the old predictor still decides
    assert s.ml_pred["X"] == 0.2                          # the model expects the price to rise 20c
    assert s.predicted_against("X", -50_000, snap) == 0.2 and s.predicted_against("X", 50_000, snap) == -0.2
    assert [pick_profile("liquidity", "alternate:A_default,rsi_ml", heat=h)[0] for h in (1, 2, 3)] == \
        ["A_default", "rsi_ml", "A_default"]


def test_crowd_guard_hands_the_sprint_back_to_the_run_and_book_model(tmp_path):
    from ritc.core.bot import Context, Snapshot
    from ritc.core.execution import Executor
    from ritc.core.risk import RiskManager
    from ritc.strategies.liquidity import LiquidityStrategy

    leaf = [{"leaf": True, "value": 0.2, "f": 0, "thr": 0, "left": 0, "right": 0}]
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": {"price_trees": {"kind": "trees", "baseline": 0.0, "trees": [leaf],
                                                           "cols": mlmodel.PRICE_COLS}}}))
    for guard in (False, True):
        cfg = {"case": {"tickers": ["X"]}, "strategy": {"sprint_model": str(path), "sprint_model_crowd_guard": guard,
                                                        "crowd_learn": True, "trend_learn": True}}
        s = LiquidityStrategy(Context(None, Executor(None), RiskManager(), cfg))
        s.sync_tickers(Snapshot({"tick": 1}, {"X": {"bid": 24.99, "ask": 25.01}}))
        for tick in range(1, 26):
            snap = Snapshot({"tick": tick, "period": 1, "ticks_per_period": 420},
                            {"X": {"bid": 24.99, "ask": 25.01, "volume": 1000 * tick}})
            snap._books["X"] = OrderBook("X")
            s._update_drift(snap)
        assert s.ml_on() and s.predicted_against("X", -50_000, snap) == 0.2       # quiet: the model decides
        s.crowd.mean, s.crowd.sd, s.crowd.n = 0.03, 0.006, 8                        # crowd alarm on (> 2 sd)
        assert s.crowded()
        if guard:   # flat mids: the run + book model expects no move, so no sprint while crowded
            assert not s.ml_on() and s.predicted_against("X", -50_000, snap) == 0.0
        else:
            assert s.ml_on() and s.predicted_against("X", -50_000, snap) == 0.2


def test_front_skip_drops_the_slice_only_when_the_move_is_expected_our_way(tmp_path):
    from ritc.core.algo import Block
    from ritc.core.bot import Context, Snapshot
    from ritc.core.execution import Executor
    from ritc.core.risk import RiskManager
    from ritc.strategies.liquidity import LiquidityStrategy

    leaf = [{"leaf": True, "value": 0.2, "f": 0, "thr": 0, "left": 0, "right": 0}]      # the model: price up 20c
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": {"price_trees": {"kind": "trees", "baseline": 0.0, "trees": [leaf],
                                                           "cols": mlmodel.PRICE_COLS}}}))
    for thr, long_skipped in ((0.0, False), (0.03, True), (0.25, False)):
        cfg = {"case": {"tickers": ["X"]}, "strategy": {"sprint_model": str(path)},
               "execution": {"front_cross": 0.25, "front_skip_favourable": thr}}
        s = LiquidityStrategy(Context(None, Executor(None), RiskManager(), cfg))
        s.sync_tickers(Snapshot({"tick": 1}, {"X": {"bid": 24.99, "ask": 25.01}}))
        for tick in range(1, 26):
            snap = Snapshot({"tick": tick, "period": 1, "ticks_per_period": 420},
                            {"X": {"bid": 24.99, "ask": 25.01, "volume": 1000 * tick}})
            snap._books["X"] = OrderBook("X")
            s._update_drift(snap)
        long_block = Block("X", 50_000, 0, 25, 60)          # we bought: a rise is in our favour -> skip the slice
        short_block = Block("X", -50_000, 0, 26, 60)        # we sold: a rise is against us -> keep the slice
        assert s.skip_front("X", long_block, snap) is long_skipped
        assert s.skip_front("X", short_block, snap) is False
        s.ml_pred["X"] = -0.2                                # decided once per block: a later prediction changes nothing
        assert s.skip_front("X", long_block, snap) is long_skipped
