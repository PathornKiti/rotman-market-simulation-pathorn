from conftest import make_book
from ritc.pricing.options import bs_price
from ritc.strategies.commodity import carry_signal, fair_basis, news_trade
from ritc.strategies.derivatives import (
    OptionSpec,
    hedge_order,
    option_signal,
    parity_trades,
    parse_option,
    portfolio_delta,
)
from ritc.strategies.equity import QuoteParams, compute_quotes, inventory_reduction
from ritc.strategies.etf import exit_signal, hedge_residuals, plan_arb
from ritc.strategies.liability import evaluate_tender, unwind_slice

# ------------------------------------------------------------------ liability
DEEP = make_book([(25.00, 20000), (24.98, 20000), (24.95, 20000)],
                 [(25.02, 20000), (25.04, 20000), (25.07, 20000)])


def test_accepts_cheap_buy_tender():
    d = evaluate_tender({"action": "BUY", "quantity": 20000, "price": 24.80, "is_fixed_bid": True},
                        DEEP, fee=0.02, min_profit=0.03)
    assert d.accept and d.profit_per_share > 0.1


def test_declines_rich_buy_tender():
    d = evaluate_tender({"action": "BUY", "quantity": 20000, "price": 25.00, "is_fixed_bid": True},
                        DEEP, fee=0.02, min_profit=0.03)
    assert not d.accept


def test_sell_tender_symmetry():
    d = evaluate_tender({"action": "SELL", "quantity": 20000, "price": 25.25, "is_fixed_bid": True},
                        DEEP, fee=0.02, min_profit=0.03)
    assert d.accept


def test_tender_respects_risk_room_and_time():
    t = {"action": "BUY", "quantity": 20000, "price": 24.0, "is_fixed_bid": True}
    assert not evaluate_tender(t, DEEP, 0.02, room=10000).accept
    assert not evaluate_tender(t, DEEP, 0.02, ticks_left=3).accept


def test_tender_size_matters():
    # Same price, but a block far bigger than the book -> slippage kills it.
    thin = make_book([(25.00, 1000), (24.80, 1000)], [(25.02, 1000)])
    t = {"action": "BUY", "quantity": 50000, "price": 24.90, "is_fixed_bid": True}
    assert not evaluate_tender(t, thin, 0.02, refill_factor=1.0).accept


def test_competitive_bid_keeps_margin():
    d = evaluate_tender({"action": "BUY", "quantity": 10000, "price": None, "is_fixed_bid": False},
                        DEEP, fee=0.02, competitive_margin=0.05, min_profit=0.03)
    assert d.accept and d.price < DEEP.best_bid
    assert abs(d.profit_per_share - 0.05) < 1e-9


def test_adverse_drift_penalises():
    t = {"action": "BUY", "quantity": 20000, "price": 24.90, "is_fixed_bid": True}
    calm = evaluate_tender(t, DEEP, 0.02)
    falling = evaluate_tender(t, DEEP, 0.02, drift_per_tick=-0.01, unwind_ticks_per_lot=0.001)
    assert falling.profit_per_share < calm.profit_per_share


def test_hold_valuation_prices_the_free_close_out_near_the_bell():
    # 2027 brief: positions left at the end close at the last traded price with no fine. Near the bell a
    # big block is worth ~mid (no fee, no book to walk), so a tender the book walk rejects is taken.
    thin = make_book([(25.00, 2000), (24.90, 2000)], [(25.02, 2000)])
    t = {"action": "BUY", "quantity": 30000, "price": 24.95, "is_fixed_bid": True}
    early = evaluate_tender(t, thin, 0.02, ticks_left=200, refill_factor=1.0, close_hold_ticks=90)
    late = evaluate_tender(t, thin, 0.02, ticks_left=60, refill_factor=1.0, close_hold_ticks=90)
    off = evaluate_tender(t, thin, 0.02, ticks_left=60, refill_factor=1.0, close_hold_ticks=0)
    assert not early.accept and not off.accept
    assert late.accept and abs(late.unwind_vwap - thin.mid) < 1e-9
    risky = evaluate_tender(t, thin, 0.02, ticks_left=60, refill_factor=1.0, close_hold_ticks=90,
                            price_vol_per_tick=0.05, risk_aversion=0.3)
    assert risky.profit_per_share < late.profit_per_share          # holding risk is still charged


def test_hold_risk_budget():
    from ritc.strategies.liability import hold_within_budget

    # 20k shares x $0.04/tick x sqrt(100 ticks) = $8k of 1-sd risk to the bell
    assert hold_within_budget(20_000, 0.04, 100, 10_000)
    assert not hold_within_budget(-20_000, 0.04, 100, 5_000)           # short side: same risk
    assert not hold_within_budget(20_000, 0.08, 100, 10_000)           # vol doubles -> cross the excess
    assert hold_within_budget(20_000, 0.08, 9, 10_000)                 # ...but not with 9 s to the bell
    assert not hold_within_budget(1_000, 0.0, 100, 10_000)             # no vol estimate yet: unwind as usual


def test_passive_only_never_crosses():
    from ritc.core.algo import AlgoParams, plan_children

    p = AlgoParams(urgent_ticks=10)
    urgent = plan_children(DEEP, 30000, 0, 0, ticks_left=2, max_order=10000, p=p)
    assert urgent and not urgent[0].passive                        # normally: cross at the deadline
    held = plan_children(DEEP, 30000, 0, 0, ticks_left=2, max_order=10000, p=p, passive_only=True)
    assert len(held) == 1 and held[0].passive and held[0].action == "SELL" and held[0].price >= DEEP.best_ask


def test_unwind_slice():
    o = unwind_slice(DEEP, 30000, max_slippage=0.02, participation=0.5, max_order=10000)
    assert o[0] == "SELL" and 0 < o[1] <= 10000
    assert unwind_slice(DEEP, 0, 0.02, 0.5, 10000) is None
    assert unwind_slice(DEEP, -500, 0.02, 0.5, 10000)[0] == "BUY"


# ---------------------------------------------------------------- derivatives
PAT = r"^(?P<und>RTM)(?P<exp>\d)(?P<cp>[CP])(?P<strike>\d+)$"


def test_parse_option():
    s = parse_option("RTM1C50", PAT, {"1": 300}, 600)
    assert s == OptionSpec("RTM1C50", True, 50.0, 300)
    assert parse_option("RTM", PAT, {}, 600) is None


def test_option_signal_buys_cheap_vol_and_sells_rich_vol():
    spec = OptionSpec("RTM1C50", True, 50, 300)
    T = 0.08
    cheap = bs_price(50, 50, T, 0, 0.15, True)
    sig = option_signal(spec, 50, cheap - 0.02, cheap + 0.02, T, 0, forecast=0.25, vol_edge=0.02,
                        full_edge=0.06, max_contracts=60, fee_per_unit=0.03)
    assert sig.target == 60 and abs(sig.iv - 0.15) < 0.01
    rich = bs_price(50, 50, T, 0, 0.35, True)
    sig = option_signal(spec, 50, rich - 0.02, rich + 0.02, T, 0, 0.25, 0.02, 0.06, 60, 0.03)
    assert sig.target == -60
    fair = bs_price(50, 50, T, 0, 0.25, True)
    sig = option_signal(spec, 50, fair - 0.02, fair + 0.02, T, 0, 0.25, 0.02, 0.06, 60, 0.03)
    assert sig.target == 0


def test_delta_and_hedge():
    d = portfolio_delta({"RTM": -1000, "C": 50}, {"C": 0.5}, "RTM", 100)
    assert d == 1500
    assert hedge_order(1500, 7000, 0.5, 10000) is None
    assert hedge_order(5000, 7000, 0.5, 10000) == ("SELL", 5000)
    assert hedge_order(-5000, 7000, 0.5, 2000) == ("BUY", 2000)


def test_parity_trades():
    # Call rich vs put+stock: C=6, P=1, S=50, K=50 -> conversion
    assert parity_trades(6.0, 6.1, 0.9, 1.0, 49.99, 50.0, 50, 0.0, 0.0, 0.1)[0] == ("call", "SELL")
    assert parity_trades(1.0, 1.1, 1.0, 1.1, 49.99, 50.0, 50, 0.0, 0.0, 0.1) is None


# ------------------------------------------------------------------------ ETF
def _etf_books(etf_bid, etf_ask):
    etf = make_book([(etf_bid, 5000), (etf_bid - 0.05, 5000)], [(etf_ask, 5000), (etf_ask + 0.05, 5000)])
    a = make_book([(19.99, 5000), (19.95, 5000)], [(20.01, 5000), (20.05, 5000)])
    b = make_book([(14.99, 5000), (14.95, 5000)], [(15.01, 5000), (15.05, 5000)])
    return etf, {"A": a, "B": b}


W = {"A": 1.0, "B": 1.0}
F = {"E": 0.02, "A": 0.02, "B": 0.02}


def test_etf_rich_sells_etf():
    etf, comps = _etf_books(35.30, 35.32)
    p = plan_arb(etf, comps, W, F, "E", entry_edge=0.10, max_qty=20000, lot=500)
    assert p.direction == "SELL_ETF" and p.edge_per_unit >= 0.10 and p.etf_qty <= 10000


def test_etf_cheap_buys_etf():
    etf, comps = _etf_books(34.70, 34.72)
    p = plan_arb(etf, comps, W, F, "E", entry_edge=0.10, max_qty=20000, lot=500)
    assert p.direction == "BUY_ETF"


def test_etf_fair_no_trade():
    etf, comps = _etf_books(34.99, 35.01)
    assert plan_arb(etf, comps, W, F, "E", 0.10, 20000, 500) is None


def test_etf_size_stops_where_edge_ends():
    etf, comps = _etf_books(35.30, 35.32)
    # Touch edge = 35.30 - 35.02 - 0.06 = 0.22; walking deeper erodes the blended edge.
    strict = plan_arb(etf, comps, W, F, "E", entry_edge=0.20, max_qty=20000, lot=500)
    loose = plan_arb(etf, comps, W, F, "E", entry_edge=0.12, max_qty=20000, lot=500)
    assert strict.edge_per_unit >= 0.20 and loose.edge_per_unit >= 0.12
    assert 5000 <= strict.etf_qty < loose.etf_qty <= 10000


def test_hedge_residuals_and_exit():
    assert hedge_residuals({"E": -1000, "A": 1000, "B": 400}, "E", W) == {"B": 600}
    assert exit_signal(-1000, 0.005, 0.01) and not exit_signal(-1000, 0.05, 0.01)
    assert exit_signal(1000, -0.005, 0.01) and not exit_signal(0, 0, 0.01)


# --------------------------------------------------------------------- equity
P = QuoteParams(min_half_spread=0.02, vol_mult=1.0, skew_per_share=0.00001, imbalance_lean=0.0,
                size=2000, max_inventory=10000)
WIDE = make_book([(9.90, 1000)], [(10.10, 1000)])


def test_quotes_flat_inventory_symmetric():
    q = compute_quotes(WIDE, 0, 0.0, P)
    assert q.bid == 9.98 and q.ask == 10.02 and q.bid_size == q.ask_size == 2000


def test_quotes_skew_with_inventory():
    q = compute_quotes(WIDE, 5000, 0.0, P)            # long -> shade down, sell more
    assert q.ask < 10.02 and q.ask_size > q.bid_size


def test_quotes_never_cross_and_stop_at_cap():
    tight = make_book([(10.00, 1000)], [(10.01, 1000)])
    q = compute_quotes(tight, 0, 0.0, P)
    assert q.bid <= 10.00 and q.ask >= 10.01
    q = compute_quotes(WIDE, 10000, 0.0, P)
    assert q.bid is None and q.ask is not None


def test_quotes_widen_with_vol():
    q = compute_quotes(WIDE, 0, 0.05, P)
    assert q.ask - q.bid >= 0.10 - 1e-9


def test_inventory_reduction():
    assert inventory_reduction(5000, 10000, 5000) is None
    assert inventory_reduction(12000, 10000, 5000) == ("SELL", 5000)


# ------------------------------------------------------------------ commodity
def test_carry_signal():
    basis = fair_basis(0.002, 100)       # 0.20
    assert carry_signal(70.0, 70.02, 70.50, 70.52, basis, 0.04, 0.1).direction == 1
    assert carry_signal(70.0, 70.02, 69.80, 69.82, basis, 0.04, 0.1).direction == -1
    assert carry_signal(70.0, 70.02, 69.80, 69.82, basis, 0.04, 0.1, near_shortable=False).direction == 0
    assert carry_signal(70.0, 70.02, 70.21, 70.23, basis, 0.04, 0.1).direction == 0


def test_news_trade():
    assert news_trade(+2.0, 0.25, 0.1, 40, 0.75)[0] < 0      # big build -> short
    assert news_trade(-4.0, 0.25, 0.1, 40, 0.75)[0] == 40    # big draw -> full long
    assert news_trade(0.2, 0.25, 0.1, 40, 0.75)[0] == 0      # noise


def _basket_books():
    return {"BULL": make_book([(9.99, 10000)], [(10.01, 10000)], "BULL"),
            "BEAR": make_book([(14.99, 10000)], [(15.01, 10000)], "BEAR")}


def test_etf_tender_edge_against_the_basket_hedge():
    from ritc.strategies.etf import tender_edge
    w, fees = {"BULL": 1.0, "BEAR": 1.0}, {"BULL": 0.02, "BEAR": 0.02, "RITC": 0.02}
    # BUY the ETF at 24.80, hedge by selling the basket at 9.99 + 14.99 = 24.98, pay 0.04 fees.
    e = tender_edge({"action": "BUY", "quantity": 5000, "price": 24.80}, _basket_books(), w, fees)
    assert abs(e - 0.14) < 1e-9
    # SELL the ETF at 25.00, hedge by buying the basket at 25.02: a loss after fees.
    e = tender_edge({"action": "SELL", "quantity": 5000, "price": 25.00}, _basket_books(), w, fees)
    assert abs(e - (-0.06)) < 1e-9
    # Past what the (refilled) book holds, the rest is priced a nickel worse.
    big = tender_edge({"action": "BUY", "quantity": 40000, "price": 24.80}, _basket_books(), w, fees)
    assert big < 0.14


def test_etf_maker_quotes_price_the_hedge_and_rebate():
    from ritc.strategies.etf import maker_quotes
    w, fees = {"BULL": 1.0, "BEAR": 1.0}, {"BULL": 0.02, "BEAR": 0.02}
    bid, ask = maker_quotes(_basket_books(), w, fees, 2000, edge=0.02, rebate=0.01)
    # bid: basket bid 24.98 - fees 0.04 + rebate 0.01 - edge 0.02 = 24.93; ask: 25.02 + 0.04 - 0.01 + 0.02
    assert abs(bid - 24.93) < 1e-9 and abs(ask - 25.07) < 1e-9


def test_term_structure_vol_weights_each_week_by_remaining_ticks():
    from ritc.strategies.derivatives import term_structure_vol as tsv
    # Tick 38 of week 1 (20% now, next week announced 30%): a 600-tick option is mostly next-week vol...
    v = tsv(38, 600, 75, 0.20, 0, 0.30)
    assert abs(v - ((37 * 0.04 + 563 * 0.09) / 600) ** 0.5) < 1e-12
    assert tsv(38, 10, 75, 0.20, 0, 0.30) == 0.20          # ...one expiring this week is all this week
    assert tsv(38, 600, 75, 0.20, 0, None) == 0.20         # no range yet: this week's vol persists
    assert tsv(76, 100, 75, 0.20, 0, 0.30) == 0.30         # new week, its headline not in yet: use the range


def test_equity_cuts_an_assigned_block_but_not_its_own_fills():
    from ritc.core.bot import Context, Snapshot
    from ritc.core.execution import Executor
    from ritc.core.risk import RiskManager
    from ritc.strategies.equity import EquityStrategy

    cfg = {"case": {"tickers": ["X"]}, "strategy": {"block_cut": 1.0, "block_detect": 5000}}
    s = EquityStrategy(Context(None, Executor(None), RiskManager(), cfg))
    sent = []
    s.ex.limit = lambda t, a, q, p, ioc=True: sent.append((t, a, q, round(p, 2))) or []

    def snap(pos: int) -> Snapshot:
        sec = {"X": {"bid": 9.99, "ask": 10.01, "position": pos}}
        sn = Snapshot({"tick": 1, "period": 1, "ticks_per_period": 300}, sec)
        sn._books["X"] = make_book([(9.99, 5000)], [(10.01, 5000)], "X")
        return sn

    for pos in (0, 1500, 3000):                 # quote fills: small steps, nothing to cut
        s.cut_blocks(snap(pos), snap(pos).positions)
    assert sent == []
    s.cut_blocks(snap(13_000), snap(13_000).positions)       # +10k in one loop: an assigned block
    assert sent == [("X", "SELL", 10_000, 9.96)]


def test_etf_fx_hedge_flattens_the_usd_balance_past_the_band():
    from ritc.core.bot import Context
    from ritc.core.execution import Executor
    from ritc.core.risk import RiskManager
    from ritc.strategies.etf import ETFStrategy

    class Client:
        def __init__(self):
            self.sent = []

        def market_order(self, t, a, q):
            self.sent.append((t, a, q))

    cfg = {"case": {"etf": "RITC", "components": {"BULL": 1.0, "BEAR": 1.0}, "fx_ticker": "USD",
                    "max_order": {"USD": 2_500_000}},
           "strategy": {"fx_hedge_band": 50_000}}
    c = Client()
    s = ETFStrategy(Context(c, Executor(c, dry_run=False, max_order_size={"USD": 2_500_000}), RiskManager(), cfg))
    s.hedge_fx({"USD": -40_000})                             # inside the band: leave it
    assert c.sent == []
    pos = {"USD": -3_000_000}
    s.hedge_fx(pos)                                          # short USD: buy it back, one max order
    assert c.sent == [("USD", "BUY", 2_500_000)] and pos["USD"] == -500_000


def test_auction_ignores_a_zero_or_absurd_reference_price():
    from ritc.core.book import Level, OrderBook
    # If the real API sends price=0 on a competitive tender, min(bid, 0) used to make us bid $0 (never filled).
    book = OrderBook("X", [Level(9.99, 50_000)], [Level(10.01, 50_000)])
    base = {"action": "BUY", "quantity": 10_000, "is_fixed_bid": False}
    none = evaluate_tender({**base, "price": None}, book, fee=0.02, min_profit=0.0)
    zero = evaluate_tender({**base, "price": 0}, book, fee=0.02, min_profit=0.0)
    silly = evaluate_tender({**base, "price": 2.50}, book, fee=0.02, min_profit=0.0)
    near = evaluate_tender({**base, "price": 9.80}, book, fee=0.02, min_profit=0.0)
    assert none.price == zero.price == silly.price > 9.5          # ignored
    assert near.price == 9.80                                      # a sane reference still caps the bid
