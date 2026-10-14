"""Replay a journal and split every fixed tender's charge (edge - model value) into its parts:
book walk (exit vwap vs mid), fee, drift x unwind time, risk premium, crowd/holder/trend charge."""
import json, sys, logging
sys.path.insert(0, '/Users/pathorn.kit/rotman-market-simulation-pathorn/docs/results')
sys.path.insert(0, '/Users/pathorn.kit/rotman-market-simulation-pathorn/src')
import journal_replay as jr
import ritc.strategies.liability as L
orig = L.evaluate_tender
REC = {}
def wrapped(tender, book, **kw):
    d = orig(tender, book, **kw)
    if tender.get('is_fixed_bid', True) and tender.get('price') is not None and book.mid and d.unwind_vwap:
        sign = 1 if str(tender.get('action')).upper() == 'BUY' else -1
        z = lambda **o: orig(tender, book, **{**kw, **o}).profit_per_share
        base = d.profit_per_share
        parts = dict(
            walk=sign * (book.mid - d.unwind_vwap),
            drift=z(drift_per_tick=0.0) - base,
            risk=z(drift_per_tick=0.0, risk_aversion=0.0) - z(drift_per_tick=0.0),
            crowd=z(drift_per_tick=0.0, risk_aversion=0.0, crowd_cost=0.0) - z(drift_per_tick=0.0, risk_aversion=0.0),
            mode='flow' if kw.get('flow_rate', 0) > 0 else 'book',
            utick=(tender['quantity'] / kw['flow_rate'] if kw.get('flow_rate', 0) > 0 else tender['quantity'] * kw.get('unwind_ticks_per_lot', 0)),
            edge=sign * (book.mid - float(tender['price'])), pps=base, flowval=None)
        if kw.get('flow_rate', 0) <= 0:
            pass
        REC.setdefault(int(tender.get('tender_id', -1)), []).append(parts)
    return d
L.evaluate_tender = wrapped
if __name__ == '__main__':
    lines = jr.replay(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    out = {}
    for tid, lst in REC.items():
        out[tid] = lst[-1]          # the last valuation = the one answered
    json.dump(out, sys.stdout)
