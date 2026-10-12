"""Simulator options for the Liquidity Risk Case: Kyle flow -> price coupling and late auction awards."""

import os


def test_sim_options_off_leave_the_market_unchanged_and_late_award_hides_the_answer():
    from ritc.sim.server import Market

    def path(stress: str) -> list[float]:
        os.environ["RITC_STRESS"] = stress
        try:
            m = Market("liability", 420, 1, seed=3)
            m.status = "ACTIVE"
            out = []
            for _ in range(50):
                m.advance()
                out.append(round(sum(x.mid for x in m.secs.values()), 9))
            return out
        finally:
            os.environ.pop("RITC_STRESS", None)

    assert path("real=1") == path("real=1,kyle=0,late_award=0,reserve_hi=0.15")
    assert path("real=1") != path("real=1,kyle=0.5")

    from ritc.sim.server import route

    def bid(stress: str, far: bool) -> tuple[dict, int]:
        os.environ["RITC_STRESS"] = stress
        try:
            m = Market("liability", 420, 1, seed=3)
            m.status = "ACTIVE"
            m.tenders[99] = {"tender_id": 99, "ticker": "CRZY", "action": "BUY", "quantity": 50_000,
                             "is_fixed_bid": False, "price": None, "expires": 20, "caption": "Client wants",
                             "_reserve": 9.90, "_rival": None}
            _, resp = route(m, "POST", "/tenders/99", {"price": "9.50" if far else "9.95"})
            for _ in range(21):
                m.advance()
            return resp, m.secs["CRZY"].position
        finally:
            os.environ.pop("RITC_STRESS", None)

    assert bid("", far=True) == ({"success": False}, 0)          # old sim: refused at once
    assert bid("late_award=1", far=True) == ({"success": True}, 0)  # real server: no answer, nothing books
    assert bid("late_award=1", far=False) == ({"success": True}, 50_000)   # past the reserve: books at expiry
