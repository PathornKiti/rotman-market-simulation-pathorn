"""Practice-server recorder analysis (`ritc calibrate`): fill model, crowd event study, pooling."""

from ritc.recorder import crowd_event_study, fill_model, flow_persistence, summarise_crowd


def _tick(k: int, mid: float, pos: int = 0) -> dict:
    book = {"bids": [{"price": round(mid - 0.01, 2), "quantity": 5000, "quantity_filled": 0}],
            "asks": [{"price": round(mid + 0.01, 2), "quantity": 5000, "quantity_filled": 0}]}
    return {"case": {"tick": k}, "books": {"X": book}, "securities": [{"ticker": "X", "position": pos}]}


def test_fill_model_measures_how_far_trades_reach_and_skips_our_own_ticks():
    # Every tick the market lifts offers 1 cent above the mid; every 4th tick a buyer sweeps 3 cents up.
    ticks = list(range(1, 101))
    by_tick = {k: _tick(k, 10.00) for k in ticks}
    rows = [{"tas": {"X": [{"tick": k, "price": 10.01, "quantity": 100}]
                     + ([{"tick": k, "price": 10.03, "quantity": 100}] if k % 4 == 0 else [])}} for k in ticks]
    f = fill_model(rows, by_tick, ticks, "X")
    assert f["sell_at"]["0.005"] == 1.0                    # the touch is reached every tick
    assert 0.2 < f["sell_at"]["0.025"] < 0.3               # 3 cents out: one tick in four
    assert f["buy_at"]["0.005"] == 0.0                     # nobody sold below the mid
    assert f["sell_at_kappa"] > 0                          # fill probability decays with distance
    by_tick_ours = {k: _tick(k, 10.00, pos=k) for k in ticks}   # our position moves every tick: all excluded
    assert fill_model(rows, by_tick_ours, ticks, "X") == {"ticks": 0}


def test_crowd_event_study_signs_moves_against_the_holders_and_pools():
    ticks = list(range(1, 60))
    by_tick = {k: _tick(k, 10.00 if k < 20 else 9.80) for k in ticks}   # price falls 20c at tick 20
    tenders = [{"ticker": "X", "quantity": 20_000, "action": "BUY", "is_fixed_bid": True,
                "first_seen": 12, "expires": 40}]                       # holders are long: a fall is "against"
    raw = crowd_event_study(by_tick, ticks, tenders)
    cells = summarise_crowd(raw)
    assert cells["X|arrival"]["mean_per_10k"] == 0.1                  # +0.20 against / 2 x 10k shares
    assert cells["X|expiry"]["mean_per_10k"] == 0.0
    pooled = summarise_crowd({k: raw[k] + raw[k] + raw[k] for k in raw})
    assert pooled["X|arrival"]["n"] == 3


def test_flow_persistence_sees_flow_that_leads_a_trending_price():
    # Regimes of 60 ticks: buyers dominate and the mid rises 1c a tick, then sellers and it falls.
    ticks = list(range(1, 401))
    mid, mids = 10.0, {}
    for k in ticks:
        up = (k // 60) % 2 == 0
        mid += 0.01 if up else -0.01
        mids[k] = round(mid, 2)
    by_tick = {k: _tick(k, mids[k]) for k in ticks}
    rows = [{"tas": {"X": [{"tick": k, "price": mids[k] + (0.01 if (k // 60) % 2 == 0 else -0.01),
                            "quantity": 1000}]}} for k in ticks]
    f = flow_persistence(rows, by_tick, ticks, "X")
    assert f["flow_next_r"] > 0.2 and f["variance_ratio"] > 2      # flow leads the move, prices trend
